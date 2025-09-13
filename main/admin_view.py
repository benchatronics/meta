# meta_search/admin_view.py
from __future__ import annotations
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import (
    # users / progress
    CustomUser, ensure_task_progress, UserTaskProgress,
    # wallet
    Wallet, WalletTxn,
    # withdrawals
    WithdrawalRequest, WithdrawalStatus, PayoutAddress,
    # deposits
    DepositRequest, DepositStatus,
    # content
    InfoPage, Announcement,
    # tasks
    UserTaskTemplate, ForcedTaskDirective, UserTask,
    tasksettngs,
)

# ---------------------------------------------------------------------
# Helpers / Guards
# ---------------------------------------------------------------------

def staff_or_manager(user):
    """Allow is_staff OR members of 'managers' group."""
    return user.is_active and (user.is_staff or user.groups.filter(name="managers").exists())

def _paginate(qs, request, per_page=25, page_param="page"):
    page = request.GET.get(page_param) or 1
    return Paginator(qs, per_page).get_page(page)

def _int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def _decimal(value, default=Decimal("0.00")):
    try:
        return Decimal(str(value))
    except Exception:
        return default

# common active_page names for highlighting in templates
AP = {
    "dash": "bo_dashboard",
    "wd":   "bo_withdrawals",
    "dep":  "bo_deposits",
    "usr":  "bo_users",
    "usr_d":"bo_user_detail",
    "wtx":  "bo_wallet_txns",
    "addr": "bo_payout_addresses",
    "set":  "bo_settings",
    "tpl":  "bo_templates",
    "dir":  "bo_directives",
    "tsk":  "bo_tasks",
}

WITHDRAW_KINDS = ["WITHDRAW", "PAYOUT", "CASH_OUT"]

# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_dashboard(request):
    # KPI counts
    ctx = {
        "active_page": AP["dash"],
        "w_pending": WithdrawalRequest.objects.filter(status=WithdrawalStatus.PENDING).count(),
        "w_confirmed": WithdrawalRequest.objects.filter(status=WithdrawalStatus.CONFIRMED).count(),
        "w_failed": WithdrawalRequest.objects.filter(status=WithdrawalStatus.FAILED).count(),
        "d_pending": DepositRequest.objects.filter(status=DepositStatus.AWAITING_PAYMENT).count(),
        "d_review":  DepositRequest.objects.filter(status=DepositStatus.AWAITING_REVIEW).count(),
        "d_confirmed": DepositRequest.objects.filter(status=DepositStatus.CONFIRMED).count(),
        "users_total": CustomUser.objects.count(),
    }

    # Short list used by the "Pending Withdrawals" card in the dashboard template
    ctx["withdrawals"] = (
        WithdrawalRequest.objects
        .select_related("user")
        .filter(status=WithdrawalStatus.PENDING)
        .order_by("-created_at")[:10]
    )

    # Optional "Recent Activity" feed (deposits/withdrawals/ledger)
    # Build a lightweight list of dicts and sort by created_at.
    recent_deposits = (
        DepositRequest.objects
        .select_related("user")
        .order_by("-created_at")[:8]
    )
    recent_withdrawals = (
        WithdrawalRequest.objects
        .select_related("user")
        .order_by("-created_at")[:8]
    )
    recent_txns = (
        WalletTxn.objects
        .select_related("wallet", "wallet__user")
        .order_by("-created_at")[:8]
    )

    activity = []
    for d in recent_deposits:
        try:
            uname = getattr(d.user, "display_name", None) or d.user.phone or f"User#{d.user_id}"
        except Exception:
            uname = f"User#{d.user_id}"
        activity.append({
            "icon": "💶",
            "title": f"Deposit {d.reference} ({d.get_status_display()})",
            "subtitle": f"{uname} • €{d.amount_cents/100:.2f}",
            "created_at": d.created_at,
        })

    for w in recent_withdrawals:
        try:
            uname = getattr(w.user, "display_name", None) or w.user.phone or f"User#{w.user_id}"
        except Exception:
            uname = f"User#{w.user_id}"
        activity.append({
            "icon": "🏧",
            "title": f"Withdrawal #{w.id} ({w.get_status_display()})",
            "subtitle": f"{uname} • €{w.amount_cents/100:.2f}",
            "created_at": w.created_at,
        })

    for t in recent_txns:
        try:
            uname = getattr(t.wallet.user, "display_name", None) or t.wallet.user.phone or f"User#{t.wallet.user_id}"
        except Exception:
            uname = "User"
        sign = "+" if (t.amount_cents or 0) >= 0 else "-"
        activity.append({
            "icon": "📒",
            "title": f"Ledger {t.kind}/{t.bucket} {sign}€{abs(t.amount_cents)/100:.2f}",
            "subtitle": uname,
            "created_at": t.created_at,
        })

    activity.sort(key=lambda x: x["created_at"] or timezone.now(), reverse=True)
    ctx["activity"] = activity[:10]

    return render(request, "meta_search/admin_dashboard.html", ctx)

# ---------------------------------------------------------------------
# Withdrawals
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_withdrawals(request):
    status = (request.GET.get("status") or "pending").lower()  # pending|confirmed|failed|all
    q      = (request.GET.get("q") or "").strip()

    qs = (
        WithdrawalRequest.objects
        .select_related("user", "address")
        .order_by("-created_at")
    )
    if status in {"pending","confirmed","failed"}:
        qs = qs.filter(status=status)

    if q:
        qs = qs.filter(
            Q(user__phone__icontains=q) |
            Q(user__nickname__icontains=q) |
            Q(user__email__icontains=q) |
            Q(id__icontains=q)
        )

    page_obj = _paginate(qs, request, per_page=25)

    return render(request, "meta_search/bo/withdrawals.html", {
        "active_page": AP["wd"],
        "page_obj": page_obj,
        "status": status,
        "q": q,
    })

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_withdrawal_approve(request, pk: int):
    if request.method != "POST":
        return redirect(reverse("bo_withdrawals"))

    wr = get_object_or_404(WithdrawalRequest, pk=pk)
    if wr.status != WithdrawalStatus.PENDING:
        messages.info(request, "This withdrawal is not pending.")
        return redirect(request.META.get("HTTP_REFERER", reverse("bo_withdrawals")))

    wallet = wr.user.wallet
    total_cents = int(wr.amount_cents) + int(wr.fee_cents)

    # Idempotent debit and ledger row
    wallet.debit_once(
        total_cents,
        bucket="CASH",
        kind="WITHDRAW",
        memo=f"Withdrawal #{wr.id}",
        external_ref=f"wd:{wr.id}",
        created_by=request.user,
    )

    wr.status = WithdrawalStatus.CONFIRMED
    wr.confirmed_at = timezone.now()
    wr.save(update_fields=["status", "confirmed_at"])

    # mark progress window
    try:
        ensure_task_progress(wr.user).mark_withdraw_done()
    except Exception:
        pass

    messages.success(request, f"Withdrawal #{wr.id} approved.")
    return redirect(request.META.get("HTTP_REFERER", reverse("bo_withdrawals")))

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_withdrawal_fail(request, pk: int):
    if request.method != "POST":
        return redirect(reverse("bo_withdrawals"))

    wr = get_object_or_404(WithdrawalRequest, pk=pk)
    if wr.status != WithdrawalStatus.PENDING:
        messages.info(request, "This withdrawal is not pending.")
        return redirect(request.META.get("HTTP_REFERER", reverse("bo_withdrawals")))

    wr.status = WithdrawalStatus.FAILED
    wr.save(update_fields=["status"])
    messages.warning(request, f"Withdrawal #{wr.id} marked as failed.")
    return redirect(request.META.get("HTTP_REFERER", reverse("bo_withdrawals")))

# ---------------------------------------------------------------------
# Deposits
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_deposits(request):
    status = (request.GET.get("status") or "awaiting_review").lower()  # draft|awaiting_payment|awaiting_review|confirmed|failed|all
    q      = (request.GET.get("q") or "").strip()

    qs = (
        DepositRequest.objects
        .select_related("user", "pay_to")
        .order_by("-created_at")
    )

    if status in {"draft","awaiting_payment","awaiting_review","confirmed","failed"}:
        qs = qs.filter(status=status)

    if q:
        qs = qs.filter(
            Q(user__phone__icontains=q) |
            Q(user__nickname__icontains=q) |
            Q(user__email__icontains=q) |
            Q(reference__icontains=q) |
            Q(txid__icontains=q)
        )

    page_obj = _paginate(qs, request, per_page=25)
    return render(request, "meta_search/bo/deposits.html", {
        "active_page": AP["dep"],
        "page_obj": page_obj,
        "status": status,
        "q": q,
    })

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_deposit_move_to_review(request, pk: int):
    if request.method != "POST":
        return redirect(reverse("bo_deposits"))
    dr = get_object_or_404(DepositRequest, pk=pk)
    if dr.status not in [DepositStatus.DRAFT, DepositStatus.AWAITING_PAYMENT]:
        messages.info(request, "Deposit is not in a state that can move to review.")
        return redirect(request.META.get("HTTP_REFERER", reverse("bo_deposits")))
    dr.status = DepositStatus.AWAITING_REVIEW
    dr.verified_at = timezone.now()
    dr.save(update_fields=["status", "verified_at"])
    messages.success(request, f"Deposit #{dr.id} moved to review.")
    return redirect(request.META.get("HTTP_REFERER", reverse("bo_deposits")))

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_deposit_confirm(request, pk: int):
    if request.method != "POST":
        return redirect(reverse("bo_deposits"))

    dr = get_object_or_404(DepositRequest, pk=pk)
    if dr.status == DepositStatus.CONFIRMED:
        messages.info(request, "Already confirmed.")
        return redirect(request.META.get("HTTP_REFERER", reverse("bo_deposits")))
    if dr.status == DepositStatus.FAILED:
        messages.info(request, "This deposit is failed; cannot confirm.")
        return redirect(request.META.get("HTTP_REFERER", reverse("bo_deposits")))

    wallet = dr.user.wallet
    wallet.credit_once(
        int(dr.amount_cents),
        bucket="CASH",
        kind="DEPOSIT",
        memo=f"Deposit {dr.reference}",
        external_ref=f"dep:{dr.id}",
        created_by=request.user,
    )

    dr.status = DepositStatus.CONFIRMED
    dr.confirmed_at = timezone.now()
    dr.save(update_fields=["status", "confirmed_at"])
    messages.success(request, f"Deposit #{dr.id} confirmed.")
    return redirect(request.META.get("HTTP_REFERER", reverse("bo_deposits")))

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_deposit_fail(request, pk: int):
    if request.method != "POST":
        return redirect(reverse("bo_deposits"))
    dr = get_object_or_404(DepositRequest, pk=pk)
    if dr.status == DepositStatus.CONFIRMED:
        messages.info(request, "Deposit already confirmed; cannot fail now.")
        return redirect(request.META.get("HTTP_REFERER", reverse("bo_deposits")))
    dr.status = DepositStatus.FAILED
    dr.save(update_fields=["status"])
    messages.warning(request, f"Deposit #{dr.id} marked as failed.")
    return redirect(request.META.get("HTTP_REFERER", reverse("bo_deposits")))

# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_users(request):
    q      = (request.GET.get("q") or "").strip()
    blocked = request.GET.get("blocked") == "1"

    qs = CustomUser.objects.all().order_by("-date_joined")
    if q:
        qs = qs.filter(
            Q(phone__icontains=q) |
            Q(nickname__icontains=q) |
            Q(email__icontains=q)
        )

    if blocked:
        qs = qs.filter(task_progress__is_blocked=True)

    page_obj = _paginate(qs, request, per_page=25)
    return render(request, "meta_search/bo/users.html", {
        "active_page": AP["usr"],
        "page_obj": page_obj,
        "q": q, "blocked": blocked,
    })

@login_required
@user_passes_test(staff_or_manager)
def bo_user_detail(request, user_id: int):
    user = get_object_or_404(CustomUser, pk=user_id)
    # ensure progress row exists for actions
    prog = ensure_task_progress(user)
    wallet = getattr(user, "wallet", None)
    txns = WalletTxn.objects.filter(wallet=wallet).order_by("-created_at")[:50] if wallet else []

    return render(request, "meta_search/bo/user_detail.html", {
        "active_page": AP["usr_d"],
        "obj": user,
        "wallet": wallet,
        "progress": prog,
        "txns": txns,
        "addresses": user.payout_addresses.all(),
        "pending_withdrawals": user.withdrawals.filter(status=WithdrawalStatus.PENDING)[:20],
        "deposits_recent": user.deposits.order_by("-created_at")[:20],
    })

# ---- User actions ----

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_user_wallet_credit(request, user_id: int):
    if request.method != "POST":
        return redirect(reverse("bo_user_detail", args=[user_id]))
    user = get_object_or_404(CustomUser, pk=user_id)
    amt_cents = _int(request.POST.get("amount_cents"), 0)
    memo = (request.POST.get("memo") or "").strip() or "Manual credit"
    ext  = (request.POST.get("external_ref") or "").strip()
    if amt_cents <= 0:
        messages.error(request, "amount_cents must be positive.")
        return redirect(reverse("bo_user_detail", args=[user_id]))
    user.wallet.credit_once(amt_cents, bucket="CASH", kind="ADJUST", memo=memo, external_ref=ext, created_by=request.user)
    messages.success(request, f"Credited €{amt_cents/100:.2f} to {user.display_name}.")
    return redirect(reverse("bo_user_detail", args=[user_id]))

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_user_wallet_debit(request, user_id: int):
    if request.method != "POST":
        return redirect(reverse("bo_user_detail", args=[user_id]))
    user = get_object_or_404(CustomUser, pk=user_id)
    amt_cents = _int(request.POST.get("amount_cents"), 0)
    memo = (request.POST.get("memo") or "").strip() or "Manual debit"
    ext  = (request.POST.get("external_ref") or "").strip()
    if amt_cents <= 0:
        messages.error(request, "amount_cents must be positive.")
        return redirect(reverse("bo_user_detail", args=[user_id]))
    user.wallet.debit_once(amt_cents, bucket="CASH", kind="ADJUST", memo=memo, external_ref=ext, created_by=request.user)
    messages.success(request, f"Debited €{amt_cents/100:.2f} from {user.display_name}.")
    return redirect(reverse("bo_user_detail", args=[user_id]))

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_user_unblock(request, user_id: int):
    if request.method != "POST":
        return redirect(reverse("bo_user_detail", args=[user_id]))
    prog = ensure_task_progress(get_object_or_404(CustomUser, pk=user_id))
    prog.unblock()
    messages.success(request, "User unblocked and cycle reset.")
    return redirect(reverse("bo_user_detail", args=[user_id]))

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_user_clear_txpin(request, user_id: int):
    if request.method != "POST":
        return redirect(reverse("bo_user_detail", args=[user_id]))
    user = get_object_or_404(CustomUser, pk=user_id)
    user.tx_pin_hash = ""
    user.tx_pin_attempts = 0
    user.tx_pin_locked_until = None
    user.save(update_fields=["tx_pin_hash","tx_pin_attempts","tx_pin_locked_until"])
    messages.success(request, "Withdrawal PIN cleared.")
    return redirect(reverse("bo_user_detail", args=[user_id]))

# ---------------------------------------------------------------------
# Wallet Txns & Payout Addresses (read-only screens)
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_wallet_txns(request, user_id: int):
    user = get_object_or_404(CustomUser, pk=user_id)
    wallet = getattr(user, "wallet", None)
    qs = WalletTxn.objects.filter(wallet=wallet).order_by("-created_at") if wallet else WalletTxn.objects.none()
    page_obj = _paginate(qs, request, per_page=50)
    return render(request, "meta_search/bo/wallet_txns.html", {
        "active_page": AP["wtx"],
        "obj": user, "wallet": wallet, "page_obj": page_obj,
    })

@login_required
@user_passes_test(staff_or_manager)
def bo_payout_addresses(request, user_id: int):
    user = get_object_or_404(CustomUser, pk=user_id)
    return render(request, "meta_search/bo/payout_addresses.html", {
        "active_page": AP["addr"],
        "obj": user,
        "addresses": user.payout_addresses.all(),
    })

# ---------------------------------------------------------------------
# Task Settings (singleton)
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_settings(request):
    s = tasksettngs.load()
    if request.method == "POST":
        s.task_limit_per_cycle      = _int(request.POST.get("task_limit_per_cycle"), s.task_limit_per_cycle)
        s.block_on_reaching_limit   = bool(request.POST.get("block_on_reaching_limit"))
        s.block_message             = (request.POST.get("block_message") or s.block_message)
        s.task_price                = _decimal(request.POST.get("task_price"), s.task_price)
        s.task_commission           = _decimal(request.POST.get("task_commission"), s.task_commission)
        s.clear_trial_bonus_at_limit= bool(request.POST.get("clear_trial_bonus_at_limit"))
        s.cycles_between_withdrawals= _int(request.POST.get("cycles_between_withdrawals"), s.cycles_between_withdrawals)
        s.save()
        messages.success(request, "Settings saved.")
        return redirect(reverse("bo_settings"))
    return render(request, "meta_search/bo/settings.html", {
        "active_page": AP["set"],
        "s": s,
    })

# ---------------------------------------------------------------------
# Task Templates (basic list & toggle)
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_templates(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip().upper()
    qs = UserTaskTemplate.objects.all().order_by("-updated_at")
    if q:
        qs = qs.filter(Q(hotel_name__icontains=q) | Q(city__icontains=q) | Q(country__icontains=q) | Q(task_id__icontains=q))
    if status:
        qs = qs.filter(status=status)
    page_obj = _paginate(qs, request, per_page=25)
    return render(request, "meta_search/bo/templates.html", {
        "active_page": AP["tpl"], "page_obj": page_obj, "q": q, "status": status
    })

@login_required
@user_passes_test(staff_or_manager)
def bo_template_toggle_status(request, tpl_id: int):
    if request.method != "POST":
        return redirect(reverse("bo_templates"))
    tpl = get_object_or_404(UserTaskTemplate, pk=tpl_id)
    new_status = request.POST.get("status") or ""
    allowed = {c for c, _ in UserTaskTemplate.Status.choices}
    if new_status not in allowed:
        messages.error(request, "Invalid status.")
    else:
        tpl.status = new_status
        tpl.save(update_fields=["status"])
        messages.success(request, f"Template #{tpl.id} status set to {new_status}.")
    return redirect(reverse("bo_templates"))

# ---------------------------------------------------------------------
# Forced Directives (list, create, cancel)
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_directives(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "PENDING").upper()
    qs = ForcedTaskDirective.objects.select_related("user", "template").order_by("-created_at")
    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(user__nickname__icontains=q))
    if status:
        qs = qs.filter(status=status)
    page_obj = _paginate(qs, request, per_page=25)
    return render(request, "meta_search/bo/directives.html", {
        "active_page": AP["dir"], "page_obj": page_obj, "q": q, "status": status
    })

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_directive_create(request):
    if request.method != "POST":
        return redirect(reverse("bo_directives"))
    user_id = _int(request.POST.get("user_id"))
    order   = _int(request.POST.get("target_order"))
    cycle   = _int(request.POST.get("applies_on_cycle"))
    tpl_id  = _int(request.POST.get("template_id"))
    reason  = (request.POST.get("reason") or "").strip()

    user = get_object_or_404(CustomUser, pk=user_id)
    tpl  = get_object_or_404(UserTaskTemplate, pk=tpl_id) if tpl_id else None

    d = ForcedTaskDirective.objects.create(
        user=user, applies_on_cycle=cycle, target_order=order,
        template=tpl, reason=reason
    )
    messages.success(request, f"Directive #{d.id} created.")
    return redirect(reverse("bo_directives"))

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_directive_cancel(request, dir_id: int):
    if request.method != "POST":
        return redirect(reverse("bo_directives"))
    d = get_object_or_404(ForcedTaskDirective, pk=dir_id)
    d.status = ForcedTaskDirective.Status.CANCELED
    d.canceled_at = timezone.now()
    d.save(update_fields=["status", "canceled_at"])
    messages.success(request, f"Directive #{d.id} canceled.")
    return redirect(reverse("bo_directives"))

# ---------------------------------------------------------------------
# User Tasks (list, approve admin)
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_tasks(request):
    status = (request.GET.get("status") or "").upper()
    kind   = (request.GET.get("kind") or "").upper()
    q      = (request.GET.get("q") or "").strip()

    qs = UserTask.objects.select_related("user", "template").order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(task_kind=kind)
    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(user__nickname__icontains=q) | Q(template__hotel_name__icontains=q))

    page_obj = _paginate(qs, request, per_page=25)
    return render(request, "meta_search/bo/tasks.html", {
        "active_page": AP["tsk"], "page_obj": page_obj, "status": status, "kind": kind, "q": q
    })

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_task_approve_admin(request, task_id: int):
    if request.method != "POST":
        return redirect(reverse("bo_tasks"))
    t = get_object_or_404(UserTask, pk=task_id)
    try:
        t.approve_admin()
        messages.success(request, f"Task #{t.id} approved.")
    except Exception as e:
        messages.error(request, f"Cannot approve: {e}")
    return redirect(request.META.get("HTTP_REFERER", reverse("bo_tasks")))

@login_required
@user_passes_test(staff_or_manager)
@transaction.atomic
def bo_task_reject(request, task_id: int):
    if request.method != "POST":
        return redirect(reverse("bo_tasks"))
    t = get_object_or_404(UserTask, pk=task_id)
    if t.status not in [UserTask.Status.IN_PROGRESS, UserTask.Status.SUBMITTED]:
        messages.info(request, "Task not in a rejectable state.")
        return redirect(request.META.get("HTTP_REFERER", reverse("bo_tasks")))
    t.status = UserTask.Status.REJECTED
    t.decided_at = timezone.now()
    t.save(update_fields=["status", "decided_at"])
    messages.warning(request, f"Task #{t.id} rejected.")
    return redirect(request.META.get("HTTP_REFERER", reverse("bo_tasks")))

# ---------------------------------------------------------------------
# Content: InfoPages & Announcements (lightweight)
# ---------------------------------------------------------------------

@login_required
@user_passes_test(staff_or_manager)
def bo_info_pages(request):
    pages = InfoPage.objects.order_by("key")
    return render(request, "meta_search/bo/info_pages.html", {
        "active_page": AP["set"], "pages": pages
    })

@login_required
@user_passes_test(staff_or_manager)
def bo_info_page_edit(request, pk: int):
    page = get_object_or_404(InfoPage, pk=pk)
    if request.method == "POST":
        page.title = request.POST.get("title") or page.title
        page.body  = request.POST.get("body") or page.body
        page.is_published = bool(request.POST.get("is_published"))
        page.save()
        messages.success(request, "Saved.")
        return redirect(reverse("bo_info_pages"))
    return render(request, "meta_search/bo/info_page_edit.html", {
        "active_page": AP["set"], "page": page
    })

@login_required
@user_passes_test(staff_or_manager)
def bo_announcements(request):
    q = (request.GET.get("q") or "").strip()
    qs = Announcement.objects.all().order_by("-created_at")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    page_obj = _paginate(qs, request, per_page=25)
    return render(request, "meta_search/bo/announcements.html", {
        "active_page": AP["set"], "page_obj": page_obj, "q": q
    })

@login_required
@user_passes_test(staff_or_manager)
def bo_announcement_edit(request, pk: int | None = None):
    obj = get_object_or_404(Announcement, pk=pk) if pk else Announcement()
    if request.method == "POST":
        obj.title = request.POST.get("title") or obj.title
        obj.body  = request.POST.get("body") or obj.body
        obj.pinned = bool(request.POST.get("pinned"))
        obj.is_published = bool(request.POST.get("is_published"))
        obj.save()
        messages.success(request, "Saved.")
        return redirect(reverse("bo_announcements"))
    return render(request, "meta_search/bo/announcement_edit.html", {
        "active_page": AP["set"], "obj": obj
    })
