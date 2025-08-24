# main/tasks.py
from __future__ import annotations

import time
import random
from typing import Optional, Tuple, Dict

from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.utils import timezone

from main.models import (
    SystemSettings,
    Task, TaskPhase, TaskStatus,
    Wallet,
    TaskTemplate,  # templates for briefs
)

# --- Optional admin override model (fallback if not present) ---
try:
    from main.models import AccountDisplay, AccountDisplayMode  # AUTO / MANUAL
except Exception:
    AccountDisplay = None
    AccountDisplayMode = None


# =========================
# Aggregate helpers (NO ledger)
# =========================

def _approved_tasks(user, phase: Optional[str] = None):
    qs = Task.objects.filter(user=user, status=TaskStatus.APPROVED)
    if phase:
        qs = qs.filter(phase=phase)
    return qs

def count_approved(user, phase: str) -> int:
    return _approved_tasks(user, phase).count()

def dividends_cents(user) -> int:
    """Dividends = sum of commission_cents from all APPROVED tasks (trial + normal + VIP)."""
    return _approved_tasks(user).aggregate(total=Sum("commission_cents"))["total"] or 0

def trial_remaining_cents(user) -> int:
    """Trial remaining mirrors the Wallet.bonus_cents bucket."""
    return user.wallet.bonus_cents

def latest_vip_task(user) -> Optional[Task]:
    return Task.objects.filter(user=user, phase=TaskPhase.VIP).order_by("-created_at").first()


# =========================
# Template selection (random, avoid repeats)
# =========================

def pick_template_for_phase(phase: str, *, user=None) -> Optional[TaskTemplate]:
    """
    Pick a published template for the given phase.
    Strategy:
      - Exclude last ~20 templates the user already used (if user is provided)
      - Random choice among remaining; fallback to newest/popular one
    """
    qs = TaskTemplate.objects.filter(phase=phase, is_published=True)

    if user is not None:
        recent_tpl_ids = list(
            Task.objects.filter(user=user, template__isnull=False)
            .order_by("-created_at")
            .values_list("template_id", flat=True)[:20]
        )
        if recent_tpl_ids:
            qs = qs.exclude(id__in=recent_tpl_ids)

    pool = list(qs)
    if pool:
        return random.choice(pool)

    # Fallback: just take the most popular/newest one
    return (
        TaskTemplate.objects
        .filter(phase=phase, is_published=True)
        .order_by("-popularity", "-created_at")
        .first()
    )


# =========================
# Phase detection (automatic)
# =========================

def detect_phase(user) -> str:
    """
    Trial -> Normal -> VIP (by approved task counts).
    - Trial lasts EXACTLY 25 tasks (fixed).
    - Normal lasts 'normal_task_limit' tasks (admin setting).
    - VIP thereafter.
    """
    s = SystemSettings.current()
    trial_done = count_approved(user, TaskPhase.TRIAL) >= 25
    if not trial_done:
        return TaskPhase.TRIAL

    normal_done = count_approved(user, TaskPhase.NORMAL) >= s.normal_task_limit
    if not normal_done:
        return TaskPhase.NORMAL

    return TaskPhase.VIP


# =========================
# VIP deposit guard
# =========================

def vip_deposit_shortfall_cents(user) -> int:
    """
    How much CASH (in cents) the user still needs to have to start the current VIP task.
    We *only* count real cash (wallet.balance_cents), not bonus.
    """
    vip = latest_vip_task(user)
    if not vip or vip.status not in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.SUBMITTED):
        return 0
    required = max(0, vip.deposit_required_cents or 0)
    has_cash = user.wallet.balance_cents or 0
    return max(0, required - has_cash)


# =========================
# Core actions
# =========================

@transaction.atomic
def complete_trial_task(user, idempotency_key: str, *, template: Optional[TaskTemplate] = None) -> Task:
    """
    Completes ONE trial task:
    - Validates phase & remaining bonus
    - Auto-approves the task
    - Debits Wallet.bonus_cents by trial cost
    - Credits Wallet.balance_cents by trial commission
    - Attaches a phase=TRIAL TaskTemplate (if provided; else pick one)
    """
    s = SystemSettings.current()

    if detect_phase(user) != TaskPhase.TRIAL:
        raise ValueError("Trial phase is over.")
    done = count_approved(user, TaskPhase.TRIAL)
    if done >= 25:
        raise ValueError("Reached max trial tasks (25).")

    existing = Task.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing

    w = Wallet.objects.select_for_update().get(user=user)
    if w.bonus_cents < s.trial_task_cost_cents:
        raise ValueError("Insufficient trial bonus for task cost.")

    tpl = template or pick_template_for_phase(TaskPhase.TRIAL, user=user)

    try:
        t = Task.objects.create(
            user=user,
            phase=TaskPhase.TRIAL,
            index_in_phase=done + 1,
            cost_cents=s.trial_task_cost_cents,
            commission_cents=s.trial_commission_cents,
            status=TaskStatus.APPROVED,  # auto-approve
            idempotency_key=idempotency_key,
            approved_at=timezone.now(),
            template=tpl,
        )
    except IntegrityError:
        t = Task.objects.get(idempotency_key=idempotency_key)

    Wallet.objects.filter(pk=w.pk).update(
        bonus_cents=w.bonus_cents - s.trial_task_cost_cents,
        balance_cents=w.balance_cents + s.trial_commission_cents,
    )
    return t


@transaction.atomic
def complete_normal_task(user, idempotency_key: str, *, template: Optional[TaskTemplate] = None) -> Task:
    """
    Completes ONE normal task:
    - Validates phase & limit
    - Auto-approves the task
    - Credits Wallet.balance_cents by normal commission
    - worth_cents defaults to SystemSettings.normal_worth_cents (often 0)
    - Attaches a phase=NORMAL TaskTemplate (if provided; else pick one)
    """
    s = SystemSettings.current()

    if detect_phase(user) != TaskPhase.NORMAL:
        raise ValueError("Not in normal phase.")
    done = count_approved(user, TaskPhase.NORMAL)
    if done >= s.normal_task_limit:
        raise ValueError("Normal task limit reached.")

    existing = Task.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing

    w = Wallet.objects.select_for_update().get(user=user)
    tpl = template or pick_template_for_phase(TaskPhase.NORMAL, user=user)

    try:
        t = Task.objects.create(
            user=user,
            phase=TaskPhase.NORMAL,
            index_in_phase=done + 1,
            cost_cents=0,
            commission_cents=s.normal_commission_cents,
            worth_cents=s.normal_worth_cents,
            status=TaskStatus.APPROVED,
            idempotency_key=idempotency_key,
            approved_at=timezone.now(),
            template=tpl,
        )
    except IntegrityError:
        t = Task.objects.get(idempotency_key=idempotency_key)

    Wallet.objects.filter(pk=w.pk).update(
        balance_cents=w.balance_cents + s.normal_commission_cents
    )
    return t


@transaction.atomic
def assign_vip_task(
    admin_user,
    user,
    *,
    worth_cents: int,
    commission_cents: int,
    deposit_required_cents: int = 0,
    idempotency_key: Optional[str] = None,
    template: Optional[TaskTemplate] = None,
) -> Task:
    """
    Admin assigns a VIP task:
    - No approval/wallet movement here.
    - If no template passed, auto-pick phase=VIP template (if available).
    """
    if detect_phase(user) != TaskPhase.VIP:
        raise ValueError("User is not yet in VIP phase.")

    last_index = Task.objects.filter(user=user, phase=TaskPhase.VIP).count()
    tpl = template or pick_template_for_phase(TaskPhase.VIP, user=user)

    t = Task.objects.create(
        user=user,
        phase=TaskPhase.VIP,
        index_in_phase=last_index + 1,
        worth_cents=worth_cents,
        commission_cents=commission_cents,
        deposit_required_cents=max(0, deposit_required_cents),
        status=TaskStatus.PENDING,
        assigned_by=admin_user,
        idempotency_key=idempotency_key or f"VIP-{user.id}-{last_index+1}-{int(time.time()*1000)}",
        template=tpl,
    )
    return t


@transaction.atomic
def approve_vip_submission(task: Task, *, idempotency_key: str) -> Task:
    """
    Approves a submitted VIP task:
    - Sets status=APPROVED
    - Credits Wallet.balance_cents by task.commission_cents
    (VIP 'worth' affects UI valuation, not wallet balance.)
    """
    if not task.is_vip:
        raise ValueError("Not a VIP task.")
    if task.status in (TaskStatus.APPROVED, TaskStatus.CANCELED, TaskStatus.REJECTED):
        return task

    task.status = TaskStatus.APPROVED
    task.approved_at = timezone.now()
    if not task.idempotency_key:
        task.idempotency_key = idempotency_key
    task.save(update_fields=["status", "approved_at", "idempotency_key"])

    w = Wallet.objects.select_for_update().get(user=task.user)
    Wallet.objects.filter(pk=w.pk).update(
        balance_cents=w.balance_cents + task.commission_cents
    )
    return task


# =========================
# Admin-editable snapshot (AUTO/MANUAL with safe fallback)
# =========================

def _get_or_create_display(user):
    if AccountDisplay is None:
        return None
    display, _ = AccountDisplay.objects.get_or_create(user=user)
    return display

def account_snapshot(user) -> Dict[str, object]:
    """
    If AccountDisplay.mode == MANUAL -> use admin-edited values.
    Else (AUTO or model missing) -> compute and (if model exists) persist for admin view.
    """
    display = _get_or_create_display(user)

    if display is not None and AccountDisplayMode is not None and display.mode == AccountDisplayMode.MANUAL:
        return {
            "total_assets_eur": f"€{display.total_assets_cents/100:,.2f}",
            "asset_eur":        f"€{display.asset_cents/100:,.2f}",
            "dividends_eur":    f"€{display.dividends_cents/100:,.2f}",
            "processing_eur":   f"€{display.processing_cents/100:,.2f}",
            "total_assets_cents": display.total_assets_cents,
            "asset_cents":        display.asset_cents,
            "dividends_cents":    display.dividends_cents,
            "processing_cents":   display.processing_cents,
            "phase": detect_phase(user),
        }

    phase = detect_phase(user)
    div_cents = dividends_cents(user)

    asset_cents = 0
    total_assets_cents = 0
    processing_cents = 0

    if phase == TaskPhase.TRIAL:
        trial_rem = trial_remaining_cents(user)
        asset_cents = trial_rem + div_cents
        total_assets_cents = asset_cents
        processing_cents = 0

    elif phase == TaskPhase.NORMAL:
        normal_worth_sum = _approved_tasks(user, TaskPhase.NORMAL).aggregate(
            total=Sum("worth_cents")
        )["total"] or 0
        asset_cents = normal_worth_sum
        total_assets_cents = asset_cents + div_cents
        processing_cents = 0

    else:  # VIP
        vip = latest_vip_task(user)
        if vip and vip.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.SUBMITTED):
            asset_cents = -abs(vip.worth_cents)
            total_assets_cents = asset_cents
            normal_worth_sum = _approved_tasks(user, TaskPhase.NORMAL).aggregate(
                total=Sum("worth_cents")
            )["total"] or 0
            processing_cents = normal_worth_sum + vip.commission_cents + max(0, vip.deposit_required_cents)
        else:
            last_approved_vip = Task.objects.filter(
                user=user, phase=TaskPhase.VIP, status=TaskStatus.APPROVED
            ).order_by("-approved_at").first()
            asset_cents = last_approved_vip.worth_cents if last_approved_vip else 0
            total_assets_cents = asset_cents + div_cents
            processing_cents = 0

    if display is not None:
        AccountDisplay.objects.filter(pk=display.pk).update(
            total_assets_cents=total_assets_cents,
            asset_cents=asset_cents,
            dividends_cents=div_cents,
            processing_cents=processing_cents,
        )

    return {
        "total_assets_eur": f"€{total_assets_cents/100:,.2f}",
        "asset_eur":        f"€{asset_cents/100:,.2f}",
        "dividends_eur":    f"€{div_cents/100:,.2f}",
        "processing_eur":   f"€{processing_cents/100:,.2f}",
        "total_assets_cents": total_assets_cents,
        "asset_cents":        asset_cents,
        "dividends_cents":    div_cents,
        "processing_cents":   processing_cents,
        "phase": phase,
    }


# =========================
# Convenience: one-button flow (dashboard CTA just navigates now)
# =========================

def do_next_task(user) -> Tuple[str, Optional[Task], Dict[str, object]]:
    """
    Kept for compatibility; we no longer auto-complete from dashboard.
    Returns (phase, None, snapshot)
    """
    phase = detect_phase(user)
    return phase, None, account_snapshot(user)
