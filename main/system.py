from decimal import Decimal
from dataclasses import dataclass
from django.db import models
from django.core.validators import MinValueValidator


# Optional: a tiny base to keep this table as a singleton (one row only)
class _SingletonModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # Always keep a single row at pk=1
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


@dataclass(frozen=True)
class TaskSettingsSnapshot:
    """A small immutable bundle you can copy onto a user's cycle later."""
    limit: int
    price: Decimal
    commission: Decimal
    block_on_limit: bool
    block_message: str


class SystemSettings(_SingletonModel):
    """
    Global knobs for the task engine
    (bonus excluded—you already handle the €300 at signup).
    """
    # How many tasks per cycle before block (e.g., 25)
    task_limit_per_cycle = models.PositiveIntegerField(
        default=25,
        validators=[MinValueValidator(1)],
        help_text="Number of tasks allowed in a cycle before block."
    )

    # Per-task amounts (use Fixed amounts for now; can extend to % later)
    task_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("12.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Optional charge per task (set 0.00 if unused)."
    )
    task_commission = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("1.45"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Commission paid to user per completed task."
    )

    # Block behavior at the end of a cycle
    block_on_reaching_limit = models.BooleanField(
        default=True,
        help_text="If true, user is blocked once they reach the cycle limit."
    )
    block_message = models.CharField(
        max_length=255,
        default="Trial limit reached. Please contact customer care to continue.",
        help_text="Message shown to the user when blocked."
    )

    # Housekeeping
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System Settings"

    def __str__(self):
        return "Global System Settings"

    # Handy bundle for snapshotting onto a cycle (no cross-imports needed)
    def to_snapshot(self) -> TaskSettingsSnapshot:
        return TaskSettingsSnapshot(
            limit=int(self.task_limit_per_cycle),
            price=self.task_price,
            commission=self.task_commission,
            block_on_limit=bool(self.block_on_reaching_limit),
            block_message=self.block_message,
        )
