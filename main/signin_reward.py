# reward.py  (drop-in)
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple

from django.contrib.auth import get_user_model
from django.db import transaction, IntegrityError
from django.utils import timezone

from .models import (
    UserTaskProgress,
    DailyCycleSnapshot,
    SigninRewardLog,
    Wallet,
    _wallet_credit_idem,
)

User = get_user_model()

# =======================
# Config
# =======================
REWARDS_EUR: List[int] = [10, 30, 50, 100, 200]
REWARDS_CENTS = [x * 100 for x in REWARDS_EUR]
BONUS_EUR = 350
BONUS_CENTS = BONUS_EUR * 100

# Where to place reward credits (withdrawable)
REWARD_BUCKET = "CASH"
REWARD_KIND = "REWARD"

# =======================
# Date helpers
# =======================
def _today():
    return timezone.localdate()

# =======================
# Per-day cycle requirement (TRIAL-BONUS RULE)
# =======================
def _required_cycles_for_date(user: User, date) -> int:
    """
    Rule:
      - Require 3 cycles if the user still has TRIAL bonus (wallet.bonus_cents > 0).
      - Otherwise 2 cycles.
    This is resilient to users skipping days; the requirement drops to 2 as soon
    as the trial bonus is cleared/debited to 0.
    """
    try:
        w = getattr(user, "wallet", None)
        bonus_left = int(getattr(w, "bonus_cents", 0) or 0)
    except Exception:
        bonus_left = 0
    return 3 if bonus_left > 0 else 2

# =======================
# Snapshot-based "cycles today"
# =======================
@transaction.atomic
def _ensure_midnight_snapshot(user: User, cycles_completed_now: int) -> DailyCycleSnapshot:
    """
    Ensure a baseline row exists for TODAY. If missing, create it with the current
    cycles_completed as the midnight baseline.
    """
    today = _today()
    snap, _ = DailyCycleSnapshot.objects.get_or_create(
        user=user,
        date=today,
        defaults={"cycles_completed_at_midnight": int(cycles_completed_now or 0)},
    )
    return snap

def _cycles_done_today(user: User, cycles_completed_now: int) -> int:
    snap = _ensure_midnight_snapshot(user, cycles_completed_now)
    base = int(snap.cycles_completed_at_midnight or 0)
    return max(0, int(cycles_completed_now or 0) - base)

# =======================
# Claim & streak helpers
# =======================
def _claimed_on(user: User, date) -> bool:
    return SigninRewardLog.objects.filter(user=user, date=date, is_bonus=False).exists()

def _last_bonus_row(user: User):
    return (
        SigninRewardLog.objects.filter(user=user, is_bonus=True)
        .order_by("-date", "-id")
        .first()
    )

def _streak_in_current_round(user: User) -> int:
    """
    Number of non-bonus claims since the last bonus (0..5).
    Using created_at ordering is fine because we unique (user, date, is_bonus).
    """
    last_bonus = _last_bonus_row(user)
    qs = SigninRewardLog.objects.filter(user=user, is_bonus=False)
    if last_bonus:
        qs = qs.filter(created_at__gt=last_bonus.created_at)
    return qs.count()

def _ext_day(user_id: int, date) -> str:
    return f"SIGNIN:U{user_id}:{date.isoformat()}"

def _ext_bonus(user_id: int, date) -> str:
    return f"SIGNIN_BONUS:U{user_id}:{date.isoformat()}"

# =======================
# Public state
# =======================
@dataclass
class SigninState:
    streak: int                 # claims in this round (0..5)
    can_claim: bool
    reason: str
    claimed_today: bool
    next_reward_cents: int
    missed_dates: List[str]     # informational list of unclaimed days since last bonus
    is_blocked: bool            # informative only (does not gate claims)

def compute_state(user: User) -> SigninState:
    prog, _ = UserTaskProgress.objects.get_or_create(user=user)

    today = _today()
    required = _required_cycles_for_date(user, today)
    done = _cycles_done_today(user, int(prog.cycles_completed or 0))
    claimed_today = _claimed_on(user, today)

    streak = _streak_in_current_round(user)
    if streak >= 5:
        return SigninState(
            streak=5,
            can_claim=False,
            reason="Round complete. Come back another day to start Day 1 again.",
            claimed_today=claimed_today,
            next_reward_cents=0,
            missed_dates=[],
            is_blocked=bool(prog.is_blocked),
        )

    # Eligibility today: meet today's cycles requirement & not already claimed
    can_claim = (done >= required) and (not claimed_today) and (streak < 5)
    next_reward_cents = REWARDS_CENTS[streak] if can_claim else 0

    # Informational "missed" dates since last bonus — days with no claim.
    # With snapshot counting, we don't retro-verify eligibility for past days.
    missed = []
    last_bonus = _last_bonus_row(user)
    start_date = (last_bonus.date if last_bonus else _today())  # start at earliest reasonable point
    # Walk forward from the day AFTER start_date up to yesterday
    cursor = start_date + timezone.timedelta(days=1)
    while cursor < today:
        if not _claimed_on(user, cursor):
            missed.append(cursor.isoformat())
        cursor += timezone.timedelta(days=1)

    reason = ""
    if claimed_today:
        reason = "Already claimed today."
    elif done < required:
        #reason = f"Login"
        reason = f"Complete more task today to unlock reward."
        #reason = f"Complete {required} cycle(s) today to unlock the reward."

    return SigninState(
        streak=streak,
        can_claim=can_claim,
        reason=reason,
        claimed_today=claimed_today,
        next_reward_cents=next_reward_cents,
        missed_dates=missed[:14],  # cap popup length
        is_blocked=bool(prog.is_blocked),
    )

# =======================
# Claim action
# =======================
@transaction.atomic
def claim_today(user: User) -> Tuple[bool, str, SigninState]:
    state = compute_state(user)
    if not state.can_claim:
        return False, state.reason or "Not eligible to claim today.", state

    today = _today()
    # 1) Create the claim row (idempotent per (user, date, is_bonus=False))
    try:
        SigninRewardLog.objects.create(
            user=user,
            date=today,
            amount_cents=state.next_reward_cents,
            is_bonus=False,
        )
    except IntegrityError:
        # already claimed today — treat as success and reflect current state
        return True, "", compute_state(user)

    # 2) Credit wallet once — put sign-in reward into CASH (withdrawable)
    wallet = getattr(user, "wallet", None) or Wallet.objects.create(user=user)
    ok = _wallet_credit_idem(
        wallet,
        int(state.next_reward_cents),
        memo=f"Sign-in Day {state.streak + 1} reward",
        bucket=REWARD_BUCKET,
        kind=REWARD_KIND,
        external_ref=_ext_day(user.id, today),
    )
    if not ok:
        raise IntegrityError("Wallet credit failed")

    # 3) If this was the 5th claim in round, credit the €350 bonus & log it
    new_streak = state.streak + 1
    if new_streak >= 5:
        _wallet_credit_idem(
            wallet,
            int(BONUS_CENTS),
            memo="5-day round bonus",
            bucket=REWARD_BUCKET,
            kind=REWARD_KIND,
            external_ref=_ext_bonus(user.id, today),
        )
        SigninRewardLog.objects.create(
            user=user,
            date=today,
            amount_cents=BONUS_CENTS,
            is_bonus=True,
        )
        # next day starts back at Day 1 (streak will reset to 0)

    return True, "", compute_state(user)
