 # user_taskview.py
from __future__ import annotations

from datetime import date

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.core.exceptions import ValidationError

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
def task_dashboard(request):
    """
    Dashboard:
      - 4 cards (Total asset, Asset, Dividends, Processing)
      - 'Order received today' counter and cycle limit
      - GET TASK button (disabled if blocked or while processing)
      - Tabs (all/completed/processing) + latest tasks list

    RULES:
      • While an ADMIN task is ASSIGNED (processing > 0):
          Total Asset == Asset == (- required)  ← show negative required (from model).
      • When NOT processing:
          Total Asset == Wallet (cash + bonus).
      • Asset never includes commissions directly.
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

    # IMPORTANT: Only force Total Asset = Wallet when NOT processing.
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

    # Disable GET TASK while processing or blocked
    #can_get_task = (not prog.is_blocked) and (processing_cents == 0)

    #To unblock the get task on taskdashboard for admin task
    # Leave GET TASK enabled unless blocked; do_task() will resume the in-flight task
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

    ctx = {
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
    }
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