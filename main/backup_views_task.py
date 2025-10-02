 # user_taskview.py
from __future__ import annotations
from django.utils.translation import gettext as _
from django.utils import timezone
from .signin_reward import compute_state, claim_today, _required_cycles_for_date
from datetime import date
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.core.exceptions import ValidationError
from .models import maybe_offer_fortune, FortuneCardRule
from django.views.decorators.http import require_POST
from django.http import JsonResponse, Http404
from .models import FortuneCardGrant, grant_cash_reward, convert_to_golden_task
from .models import UserTaskProgress


from .models import (
    ensure_task_progress,
    UserTask,
    UserTaskTemplate,
    FortuneCardRule,
    maybe_offer_fortune,
)

from .signin_reward import (
    _cycles_done_today,   # snapshot-based helper
)

from .models import (
    UserTask,
    spawn_next_task_for_user,
    ensure_task_progress,  # creates/repairs progress
)

def _eur(cents: int | None) -> str:
    try:
        cents = int(cents or 0)
    except Exception:
        cents = 0
    return f"€{cents / 100:.2f}"



@login_required
def signin_reward_page(request):
    state = compute_state(request.user)
    today = timezone.localdate()

    # snapshot-based "cycles today" (no UserCycleLog needed)
    prog, _ = UserTaskProgress.objects.get_or_create(user=request.user)
    cycles_done_today = _cycles_done_today(request.user, int(prog.cycles_completed or 0))

    ctx = {
        "active_page": "signinreward",
        "streak": state.streak,                       # 0..5
        "can_claim": state.can_claim,
        "cant_reason": state.reason,
        "claimed_today": state.claimed_today,
        "next_reward": (state.next_reward_cents // 100) if state.next_reward_cents else 0,
        "missed_dates": state.missed_dates,
        "is_blocked": state.is_blocked,
        # informational counters
        "required_cycles_today": _required_cycles_for_date(request.user, today),
        "cycles_done_today": cycles_done_today,
    }
    return render(request, "meta_search/signin_reward.html", ctx)

@login_required
def signin_reward_claim(request):
    if request.method != "POST":
        return redirect("signinreward")
    ok, reason, _state = claim_today(request.user)
    if ok:
        messages.success(request, _("Sign-in reward claimed."))
    else:
        messages.warning(request, _(reason))
    return redirect("signinreward")



# fortune card
@login_required
def task_dashboard(request):
    """
    Dashboard:
      - 4 cards (Total asset, Asset, Dividends, Processing)
      - 'Order received today' counter and cycle limit
      - GET TASK button (disabled if blocked or while processing)
      - Tabs (all/completed/processing) + latest tasks list
    """
    # Ensure progress exists
    prog = ensure_task_progress(request.user)

    # Base totals from model (already applies your admin/regular logic)
    totals = dict(prog.display_totals)  # *_cents keys
    processing_cents = int(totals.get("processing_cents", 0) or 0)

    # Wallet pieces (used only when NOT processing)
    wallet = getattr(request.user, "wallet", None)
    wallet_cash  = int(getattr(wallet, "balance_cents", 0) or 0)
    wallet_bonus = int(getattr(wallet, "bonus_cents", 0) or 0)
    wallet_total_cents = wallet_cash + wallet_bonus

    # Only force Total Asset = Wallet when NOT processing.
    if processing_cents == 0:
        totals["total_asset_cents"] = wallet_total_cents
    # else: keep totals from model so negative required shows in both Total & Asset

    # Other cards
    asset_cents     = int(totals.get("asset_cents", 0) or 0)
    dividends_cents = int(totals.get("dividends_cents", 0) or 0)

    # Counters / cycle snapshot
    orders_today = (
        UserTask.objects
        .filter(user=request.user, created_at__date=date.today())
        .count()
    )
    limit        = int(prog.limit_snapshot or 0)
    next_visible = int(prog.current_task_index or 0) + 1

    # GET TASK enabled unless blocked
    can_get_task = (not prog.is_blocked)

    # Withdrawal gating (policy unchanged)
    ok_to_withdraw, withdraw_msg = prog.can_withdraw()

    # Tabs + task list
    tab = request.GET.get("tab", "all")
    qs = (
        UserTask.objects
        .filter(user=request.user)
        .select_related("template")
        .order_by("-created_at")
    )
    if tab == "completed":
        qs = qs.filter(status=UserTask.Status.APPROVED)
    elif tab == "processing":
        qs = qs.filter(status__in=[UserTask.Status.IN_PROGRESS, UserTask.Status.SUBMITTED])
    tasks = list(qs[:20])

    # ===== Fortune (5b) =====
    grant = maybe_offer_fortune(request.user)
    fortune = None
    if grant:
        mode = "cash" if grant.kind == FortuneCardRule.Kind.CASH else "golden"
        price_cents = 0
        if mode == "golden":
            try:
                tpl = (UserTaskTemplate.objects
                       .only("id", "task_price")
                       .get(pk=grant.golden_template_id))
                price_cents = to_cents(tpl.effective_price())
            except Exception:
                price_cents = 0

        fortune = {
            "grant_id": grant.pk,
            "mode": mode,
            "amount_cents": int(grant.amount_cents or 0),
            "price_cents": int(price_cents or 0),
        }

    # --- DEBUG/QA: force the fortune modal from URL without touching models ---
    # Examples:
    #   /tasks/?force_fortune=cash:2      → cash reward €2.00
    #   /tasks/?force_fortune=golden:50   → golden card with task value €50.00
    #   /tasks/?force_fortune=cash        → default €2.00
    #   /tasks/?force_fortune=golden      → default €100.00
    force = request.GET.get("force_fortune")
    if not fortune and force:
        try:
            kind, _, amt = force.partition(":")
            kind = kind.strip().lower()
            amount = int(amt) if amt.strip().isdigit() else (2 if kind == "cash" else 100)
            if kind == "cash":
                fortune = {
                    "grant_id": 0,                # 0 = demo (don’t POST)
                    "mode": "cash",
                    "amount_cents": amount * 100,
                    "price_cents": 0,
                }
            elif kind == "golden":
                fortune = {
                    "grant_id": 0,                # 0 = demo (don’t POST)
                    "mode": "golden",
                    "amount_cents": 0,
                    "price_cents": amount * 100,
                }
        except Exception:
            pass
    # --- /force hook ---

    # Build context WITHOUT overwriting fortune
    ctx = {}
    ctx["fortune"] = fortune  # keep 5b available to template

    ctx.update({
        "active_page": "task",

        # money strings for the 4 cards
        "total_asset_eur": _eur(totals.get("total_asset_cents")),
        "asset_eur":       _eur(asset_cents),
        "dividends_eur":   _eur(dividends_cents),
        "processing_eur":  _eur(processing_cents),

        # optional wallet breakdown
        "wallet_total_eur": _eur(wallet_total_cents),
        "wallet_cash_eur":  _eur(wallet_cash),
        "wallet_bonus_eur": _eur(wallet_bonus),

        # header/button meta
        "orders_today": orders_today,
        "limit": limit,
        "next_visible": next_visible,
        "can_get_task": can_get_task,

        # withdrawal gating (unchanged)
        "can_withdraw": ok_to_withdraw,
        "withdraw_hint": withdraw_msg,

        # list + tabs
        "tab": tab,
        "tasks": tasks,
    })

    return render(request, "tasks/user_task_dashboard.html", ctx)


@login_required
def do_task(request):
    """
    Go to the user's current task if one is active; otherwise spawn the next task.
    Honors ForcedTaskDirective and blocks if the user is at limit.
    """
    ensure_task_progress(request.user)

    current = (
        UserTask.objects
        .filter(user=request.user, status__in=[UserTask.Status.IN_PROGRESS, UserTask.Status.SUBMITTED])
        .order_by("-created_at")
        .first()
    )
    if current:
        return redirect("task_detail", pk=current.pk)

    try:
        with transaction.atomic():
            task = spawn_next_task_for_user(request.user)
        return redirect("task_detail", pk=task.pk)
    except ValidationError as e:
        messages.error(request, e.messages[0] if getattr(e, "messages", None) else str(e))
    except Exception as e:
        messages.error(request, f"Could not start a task. {e!s}")
    return redirect("task_dashboard")

@login_required
def task_detail(request, pk: int):
    """
    Task details + single Submit button.
    - Regular/Trial: auto-approve on submit (no admin step).
    - Admin: submit auto-approves if funds meet solvency rule (model handles it).
    """
    task = get_object_or_404(
        UserTask.objects.select_related("template", "user"),
        pk=pk, user=request.user
    )

    if request.method == "POST" and "submit_task" in request.POST:
        try:
            task.submit()  # proof optional per your UI
            task.refresh_from_db(fields=["status"])

            if task.status == UserTask.Status.APPROVED:
                messages.success(request, "Task completed.")
            else:
                messages.success(request, "Task submitted. Awaiting approval.")
            return redirect("task_dashboard")

        except ValidationError as e:
            messages.error(request, e.messages[0] if getattr(e, "messages", None) else str(e))
        except Exception:
            messages.error(request, "Submission failed. Please try again.")

    return render(request, "tasks/task_detail.html", {"task": task, "active_page": "task"})




@login_required
@require_POST
def fortune_receive(request, pk: int):
    from django.db import transaction

    try:
        with transaction.atomic():
            grant = get_object_or_404(
                FortuneCardGrant.objects.select_for_update(),
                pk=pk, user=request.user
            )

            if grant.kind != FortuneCardRule.Kind.CASH:
                return JsonResponse({"ok": False, "error": "Not a cash grant"}, status=400)

            picked = int(request.POST.get("box") or 0)
            if grant.status == FortuneCardGrant.Status.OFFERED:
                grant.status = FortuneCardGrant.Status.CLICKED
            if picked and picked != grant.picked_box:
                grant.picked_box = picked
            grant.save(update_fields=["status", "picked_box", "updated_at"])

            # Credit wallet
            grant = grant_cash_reward(grant)

        return JsonResponse({"ok": True, "credited_cents": int(grant.amount_cents)})

    except Exception as e:
        import traceback; traceback.print_exc()
        return JsonResponse({"ok": False, "error": str(e)}, status=500)



@login_required
@require_POST
@transaction.atomic
def fortune_open(request, pk: int):
    from django.db import transaction

    try:
        with transaction.atomic():
            grant = get_object_or_404(
                FortuneCardGrant.objects.select_for_update(),
                pk=pk, user=request.user
            )
            if grant.kind != FortuneCardRule.Kind.GOLDEN:
                return JsonResponse({"ok": False, "error": "Not a golden grant"}, status=400)

            picked = int(request.POST.get("box") or 0)
            if grant.status == FortuneCardGrant.Status.OFFERED:
                grant.status = FortuneCardGrant.Status.CLICKED
            if picked and picked != grant.picked_box:
                grant.picked_box = picked
            grant.save(update_fields=["status", "picked_box", "updated_at"])

            # convert to golden task
            task = convert_to_golden_task(grant)

            # ---- solvency logic ----
            wallet = getattr(grant.user, "wallet", None)
            if not wallet:
                raise ValueError("User has no wallet")

            worth_cents = int(task.price_used or 0)
            balance_cents = int(wallet.balance_cents or 0)

            # If user can’t cover worth → simulate negative like admin task
            if balance_cents < worth_cents:
                wallet.balance_cents = balance_cents - worth_cents
                wallet.save(update_fields=["balance_cents"])
            else:
                # normal: deduct immediately
                wallet.balance_cents = balance_cents - worth_cents
                wallet.save(update_fields=["balance_cents"])

            # dividends untouched until submit
            grant.status = FortuneCardGrant.Status.CREDITED
            grant.save(update_fields=["status", "updated_at"])

        redirect_url = reverse("task_detail", kwargs={"pk": task.pk})
        return JsonResponse({"ok": True, "redirect": redirect_url})

    except Exception as e:
        import traceback; traceback.print_exc()
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


