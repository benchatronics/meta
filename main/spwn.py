#spawn code
def spawn_next_task_for_user(user) -> "UserTask":
    """
    Start (or return) the user's next task.

    Priority:
      0) If the user already has an ADMIN task in IN_PROGRESS/SUBMITTED → return it (unskippable).
      1) Exact ForcedTaskDirective match for THIS user at (cycle, next_order): PENDING & not expired.
      2) Fallback ForcedTaskDirective for THIS user with SAME order, PENDING, not expired, and
         applies_on_cycle <= current cycle (i.e., overdue admin directive). Pick the oldest applicable.
      3) Else spawn a random ACTIVE REGULAR task (never admin at random).

    Side effects:
      • When spawning from a directive → mark directive CONSUMED immediately.
      • When spawning an ADMIN task → apply dashboard “assigned” math immediately.
      • Ensures UserTaskProgress exists (brand new users / deleted rows).
    """
    from .models import (
        UserTask, UserTaskTemplate, ForcedTaskDirective,
    )
    # make sure progress row exists
    prog = ensure_task_progress(user)
    if prog.is_blocked:
        raise ValidationError("User is blocked. Contact support to continue.")

    # 0) Existing unskippable ADMIN
    existing_admin = (
        UserTask.objects
        .filter(
            user=user,
            task_kind=UserTask.Kind.ADMIN,
            status__in=[UserTask.Status.IN_PROGRESS, UserTask.Status.SUBMITTED],
        )
        .order_by("-created_at")
        .first()
    )
    if existing_admin:
        if existing_admin.status == UserTask.Status.IN_PROGRESS:
            existing_admin.mark_admin_assigned_effects()
        return existing_admin

    # Compute the user's "slot"
    next_order = prog.natural_next_order  # 1-based
    cycle = prog.cycles_completed
    now = timezone.now()

    # 1) Strict directive match (this cycle & this position)
    strict = (
        ForcedTaskDirective.objects
        .filter(
            user=user,
            applies_on_cycle=cycle,
            target_order=next_order,
            status=ForcedTaskDirective.Status.PENDING,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related("template")
        .order_by("created_at")
        .first()
    )

    directive = strict

    # 2) Fallback: same order, overdue (applies_on_cycle <= current cycle), still pending & not expired
    if not directive:
        directive = (
            ForcedTaskDirective.objects
            .filter(
                user=user,
                target_order=next_order,
                status=ForcedTaskDirective.Status.PENDING,
            )
            .filter(Q(applies_on_cycle__lte=cycle))
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .select_related("template")
            .order_by("applies_on_cycle", "created_at")  # oldest applicable first
            .first()
        )

    if directive:
        if not directive.template:
            raise ValidationError("Admin directive is missing its template.")
        tpl = directive.template
        price = tpl.effective_price()
        commission = tpl.effective_commission()

        # Always ADMIN when a directive is used
        with transaction.atomic():
            task = UserTask.objects.create(
                user=user,
                template=tpl,
                cycle_number=cycle,
                order_shown=next_order,
                status=UserTask.Status.IN_PROGRESS,
                price_used=price,
                commission_used=commission,
                task_kind=UserTask.Kind.ADMIN,
                started_at=timezone.now(),
            )
            directive.status = ForcedTaskDirective.Status.CONSUMED
            directive.consumed_at = timezone.now()
            directive.save(update_fields=["status", "consumed_at", "updated_at"])

        task.mark_admin_assigned_effects()
        return task

    # 3) No directive: random ACTIVE REGULAR task (NEVER admin randomly)
    tpl_qs = UserTaskTemplate.objects.filter(
        status=UserTaskTemplate.Status.ACTIVE,
        is_admin_task=False,
    )
    count = tpl_qs.count()
    if count == 0:
        raise ValidationError("No active regular task templates available.")

    # --- NEW: wallet (cash + bonus) solvency gate for REGULAR tasks (no deduction) ---
    wallet = getattr(user, "wallet", None)
    wallet_cash_cents  = int(getattr(wallet, "balance_cents", 0) or 0)
    wallet_bonus_cents = int(getattr(wallet, "bonus_cents", 0) or 0)
    wallet_total_cents = wallet_cash_cents + wallet_bonus_cents  # CASH + BONUS

    from .models import tasksettngs
    s = tasksettngs.load()

    def _price_cents_for(tpl_obj):
        # Use explicit template price if set, else fallback to TaskSettings.task_price
        price_dec = tpl_obj.task_price if tpl_obj.task_price is not None else s.task_price
        return to_cents(price_dec)

    # Keep your randomness but limit pool to templates with price <= wallet TOTAL
    templates = list(tpl_qs.only("id", "task_price"))
    eligible = [t for t in templates if _price_cents_for(t) <= wallet_total_cents]

    if not eligible:
        raise ValidationError("No regular tasks match your current WALLET (cash + bonus). Please deposit to unlock more tasks.")
    # -----------------------------------------------------------------------------

    # Preserve existing random behavior among eligible templates
    tpl = random.choice(eligible)

    price = tpl.effective_price()
    commission = tpl.effective_commission()

    task = UserTask.objects.create(
        user=user,
        template=tpl,
        cycle_number=cycle,
        order_shown=next_order,
        status=UserTask.Status.IN_PROGRESS,
        price_used=price,
        commission_used=commission,
        task_kind=UserTask.Kind.REGULAR,
        started_at=timezone.now(),
    )

    # keep dashboard in normal state for regular/trial
    prog.set_state_normal()
    return task
