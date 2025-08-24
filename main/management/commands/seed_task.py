# main/management/commands/seed_tasks.py
from __future__ import annotations

import time
from typing import Optional

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from main.models import (
    Wallet, SystemSettings,
    Task, TaskPhase,
    TaskTemplate,
)
from main.task import (
    complete_trial_task,
    complete_normal_task,
    assign_vip_task,
    approve_vip_submission,
    detect_phase,
    count_approved,
    account_snapshot,
)

User = get_user_model()


def pick_template(phase: str) -> Optional[TaskTemplate]:
    # same strategy as earlier helper: most popular, newest
    return (
        TaskTemplate.objects
        .filter(phase=phase, is_published=True)
        .order_by("-popularity", "-created_at")
        .first()
    )


class Command(BaseCommand):
    help = "Seed sample Task rows for a user across Trial/Normal/VIP using real business logic."

    def add_arguments(self, parser):
        # target user
        parser.add_argument("--phone", type=str, help="Target user phone (preferred)")
        parser.add_argument("--user-id", type=int, help="Target user id (fallback)")

        # how many to create
        parser.add_argument("--trial", type=int, default=5, help="Number of TRIAL tasks to complete (max 25)")
        parser.add_argument("--normal", type=int, default=0, help="Number of NORMAL tasks to complete")

        # VIP options
        parser.add_argument("--vip", action="store_true", help="Assign one VIP task after Trial/Normal")
        parser.add_argument("--vip-worth", type=int, default=20000, help="VIP worth_cents (e.g. 20000 = €200)")
        parser.add_argument("--vip-commission", type=int, default=2500, help="VIP commission_cents (e.g. 2500 = €25)")
        parser.add_argument("--vip-deposit", type=int, default=0, help="VIP deposit_required_cents")
        parser.add_argument("--vip-approve", action="store_true", help="Approve the assigned VIP task")

        # bonus helper (optional)
        parser.add_argument("--grant-bonus", type=int, default=None,
                            help="If set, ensure Wallet.bonus_cents >= this value (credits difference)")

    def _find_user(self, phone: Optional[str], user_id: Optional[int]) -> User:
        if phone:
            try:
                return User.objects.get(phone=phone.replace(" ", "").replace("-", ""))
            except User.DoesNotExist:
                raise CommandError(f"No user with phone={phone!r}")
        if user_id:
            try:
                return User.objects.get(pk=user_id)
            except User.DoesNotExist:
                raise CommandError(f"No user with id={user_id}")
        # fallback: first user
        u = User.objects.order_by("id").first()
        if not u:
            raise CommandError("No users exist. Create a user first.")
        return u

    @transaction.atomic
    def _ensure_wallet(self, user: User, grant_bonus: Optional[int]):
        w, _ = Wallet.objects.get_or_create(user=user)
        if grant_bonus is not None:
            if w.bonus_cents < grant_bonus:
                delta = grant_bonus - w.bonus_cents
                Wallet.objects.filter(pk=w.pk).update(bonus_cents=w.bonus_cents + delta)
        return w

    def handle(self, *args, **opts):
        user = self._find_user(opts.get("phone"), opts.get("user_id"))
        s = SystemSettings.current()
        self.stdout.write(self.style.NOTICE(f"Seeding tasks for user: {user!s} (id={user.pk})"))

        self._ensure_wallet(user, opts.get("grant_bonus"))

        # ---- TRIAL ----
        want_trial = max(0, int(opts["trial"]))
        existing_trial = count_approved(user, TaskPhase.TRIAL)
        room_trial = max(0, 25 - existing_trial)
        do_trial = min(want_trial, room_trial)

        for i in range(do_trial):
            idem = f"seed-trial-{user.id}-{existing_trial + i + 1}-{int(time.time()*1000)}"
            complete_trial_task(user, idempotency_key=idem)

        # ---- NORMAL ----
        want_normal = max(0, int(opts["normal"]))
        existing_normal = count_approved(user, TaskPhase.NORMAL)
        room_normal = max(0, s.normal_task_limit - existing_normal)
        do_normal = min(want_normal, room_normal)

        # make sure phase is NORMAL (i.e., 25 trials done); if not, they'll just be skipped
        for i in range(do_normal):
            idem = f"seed-normal-{user.id}-{existing_normal + i + 1}-{int(time.time()*1000)}"
            try:
                complete_normal_task(user, idempotency_key=idem)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Skipping normal task: {e}"))
                break

        # ---- VIP (optional) ----
        vip_task = None
        if opts["vip"]:
            # ensure we are in VIP phase; if not, try to complete remaining normal tasks
            if detect_phase(user) != TaskPhase.VIP:
                remaining = max(0, s.normal_task_limit - count_approved(user, TaskPhase.NORMAL))
                self.stdout.write(self.style.NOTICE(f"Not in VIP yet; completing {remaining} normal task(s) to reach VIP…"))
                for i in range(remaining):
                    idem = f"seed-normal-{user.id}-to-vip-{int(time.time()*1000)}"
                    try:
                        complete_normal_task(user, idempotency_key=idem)
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f"Couldn’t complete normal task to reach VIP: {e}"))
                        break

            if detect_phase(user) == TaskPhase.VIP:
                tpl = pick_template(TaskPhase.VIP)
                vip_task = assign_vip_task(
                    admin_user=User.objects.filter(is_superuser=True).first() or user,
                    user=user,
                    worth_cents=int(opts["vip_worth"]),
                    commission_cents=int(opts["vip_commission"]),
                    deposit_required_cents=int(opts["vip_deposit"]),
                    template=tpl,
                )
                self.stdout.write(self.style.SUCCESS(f"Assigned VIP task id={vip_task.id} worth={vip_task.worth_cents} commission={vip_task.commission_cents}"))
                if opts["vip_approve"]:
                    approve_vip_submission(vip_task, idempotency_key=f"seed-vip-approve-{vip_task.id}")
                    self.stdout.write(self.style.SUCCESS(f"Approved VIP task id={vip_task.id}"))

        # ---- Summary ----
        phase_now = detect_phase(user)
        snap = account_snapshot(user)
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Done. Current state"))
        self.stdout.write(f"Phase: {phase_now}")
        self.stdout.write(f"Trial approved:  {count_approved(user, TaskPhase.TRIAL)}/25")
        self.stdout.write(f"Normal approved: {count_approved(user, TaskPhase.NORMAL)}/{s.normal_task_limit}")
        self.stdout.write(f"VIP tasks:       {Task.objects.filter(user=user, phase=TaskPhase.VIP).count()}")
        self.stdout.write(f"Total Assets: {snap['total_assets_eur']}")
        self.stdout.write(f"Asset:        {snap['asset_eur']}")
        self.stdout.write(f"Dividends:    {snap['dividends_eur']}")
        self.stdout.write(f"Processing:   {snap['processing_eur']}")
