# ---------- Fortune helpers: history-aware versions ----------

from decimal import Decimal
from django.db import transaction
from django.http import Http404

def grant_cash_reward(grant: FortuneCardGrant):
    """
    Credit user wallet and mark grant as CREDITED (idempotent),
    and CREATE a wallet transaction so it appears in history.
    """
    if grant.kind != FortuneCardRule.Kind.CASH:
        raise Http404("Not a cash grant")

    if grant.status == FortuneCardGrant.Status.CREDITED:
        return grant  # already done

    wallet = getattr(grant.user, "wallet", None)
    if not wallet:
        raise ValueError("User has no wallet")

    memo = f"Fortune cash reward #{grant.pk}"
    ext  = f"FORTUNE_CASH#{grant.pk}"
    amount = int(grant.amount_cents or 0)

    with transaction.atomic():
        # Prefer your existing idempotent credit helper if available
        try:
            from .models import _wallet_credit_idem  # your helper used by tasks
            _wallet_credit_idem(wallet, amount,
                                memo=memo,
                                external_ref=ext,
                                kind="DEPOSIT",
                                bucket="REWARD")
        except Exception:
            # Fallback: update balance and write a txn directly (basic idempotence on external_ref)
            from .models import WalletTxn
            if not WalletTxn.objects.filter(wallet=wallet, external_ref=ext).exists():
                wallet.balance_cents = int(wallet.balance_cents or 0) + amount
                wallet.save(update_fields=["balance_cents"])
                WalletTxn.objects.create(
                    wallet=wallet,
                    amount_cents=amount,
                    kind="DEPOSIT",
                    bucket="REWARD",
                    memo=memo,
                    external_ref=ext,
                    created_by=None,
                )

        # Mark grant credited (stops popup)
        grant.status = FortuneCardGrant.Status.CREDITED
        grant.save(update_fields=["status", "updated_at"])

    return grant


@transaction.atomic
def convert_to_golden_task(grant: FortuneCardGrant) -> "UserTask":
    """
    Create ADMIN task for THIS slot via ForcedTaskDirective.
    Log an informational REQUIRED entry in wallet history (no balance change).
    """
    from .models import (
        ensure_task_progress, UserTaskTemplate, ForcedTaskDirective,
        spawn_next_task_for_user, UserTask, UserTaskProgress, WalletTxn
    )

    # lock grant
    grant = (FortuneCardGrant.objects
             .select_for_update()
             .select_related("user")
             .get(pk=grant.pk))

    if grant.kind != FortuneCardRule.Kind.GOLDEN:
        raise Http404("Not a golden grant")

    prog = ensure_task_progress(grant.user)

    tpl = (UserTaskTemplate.objects
           .only("id", "task_price", "task_commission", "is_admin_task")
           .get(pk=grant.golden_template_id))

    # Force directive for THIS exact slot
    ForcedTaskDirective.objects.create(
        user=grant.user,
        applies_on_cycle=prog.cycles_completed,
        target_order=prog.natural_next_order,
        template=tpl,
        reason="FORTUNE_GOLDEN",
    )

    # Will create ADMIN task and call mark_admin_assigned_effects()
    task = spawn_next_task_for_user(grant.user)

    # ---- Recompute REQUIRED using CASH ONLY; persist to task & dashboard ----
    task = UserTask.objects.select_for_update().get(pk=task.pk)
    price_cents = int((task.price_used or Decimal("0")) * 100)
    commission_cents = int((task.commission_used or Decimal("0")) * 100)

    wallet = getattr(grant.user, "wallet", None)
    cash_now = int(getattr(wallet, "balance_cents", 0) or 0)  # CASH only
    required = max(0, price_cents - cash_now)

    if (task.assignment_total_display_cents != cash_now) or (task.required_cash_cents != required):
        task.assignment_total_display_cents = cash_now
        task.required_cash_cents = required
        task.save(update_fields=["assignment_total_display_cents", "required_cash_cents", "updated_at"])

    prog = UserTaskProgress.objects.select_for_update().get(pk=prog.pk)
    prog.set_state_admin_assigned(
        price_cents=price_cents,
        admin_commission_cents=commission_cents,
        required_cents=required,
    )

    # ---- Write ONE history row to show the required amount (no balance change) ----
    # We store as an ADJUST (negative) with an external_ref to keep it idempotent.
    req_ext = f"FORTUNE_GOLDEN_REQ#{grant.pk}"
    if required > 0 and not WalletTxn.objects.filter(wallet=wallet, external_ref=req_ext).exists():
        WalletTxn.objects.create(
            wallet=wallet,
            amount_cents=-required,            # informational line; DOES NOT alter wallet balance elsewhere
            kind="ADJUST",
            bucket="REQUIRED",
            memo=f"Golden task required cash (non-debit) #{grant.pk}",
            external_ref=req_ext,
            created_by=None,
        )

    # finalize grant
    grant.user_task = task
    grant.status = FortuneCardGrant.Status.CONVERTED
    grant.save(update_fields=["user_task", "status", "updated_at"])

    return task
