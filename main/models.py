# models.py
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.crypto import get_random_string
from django.utils import timezone
from django.db import models, transaction
from django.db.models import F
from django.core.validators import URLValidator
from typing import Optional
from django.utils.translation import gettext_lazy as _
# models.py (top of file)
from datetime import timedelta
from django.contrib.auth.hashers import make_password, check_password

# ---------- Custom User ----------
class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra_fields):
        if not phone:
            raise ValueError("The phone number must be set")
        phone = phone.replace(" ", "").replace("-", "")
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(phone, password, **extra_fields)

class CustomUser(AbstractUser):
    username = None
    phone = models.CharField(max_length=20, unique=True)
    invitation_code = models.CharField(max_length=50, blank=True, null=True)

    # NEW — user can add these later
    nickname   = models.CharField(max_length=50, blank=True)
    avatar     = models.ImageField(upload_to="avatars/%Y/%m/", blank=True, null=True)
    avatar_url = models.URLField(blank=True, null=True, validators=[URLValidator()])

    # IP + Country tracking
    signup_ip = models.GenericIPAddressField(blank=True, null=True)
    signup_country = models.CharField(max_length=100, blank=True, null=True)
    last_login_ip = models.GenericIPAddressField(blank=True, null=True)
    last_login_country = models.CharField(max_length=100, blank=True, null=True)

    # === Withdrawal password (PIN) ===
    tx_pin_hash = models.CharField(max_length=128, blank=True)
    tx_pin_changed_at = models.DateTimeField(blank=True, null=True)
    tx_pin_attempts = models.PositiveIntegerField(default=0)
    tx_pin_locked_until = models.DateTimeField(blank=True, null=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self) -> str:
        # Prefer nickname in admin / shells
        return self.nickname or self.phone

    def save(self, *args, **kwargs):
        if self.phone:
            self.phone = self.phone.replace(" ", "").replace("-", "")
        super().save(*args, **kwargs)

    # Convenience for templates: best avatar URL to display
    @property
    def display_avatar(self) -> Optional[str]:
        if self.avatar:
            try:
                return self.avatar.url
            except Exception:
                pass
        return self.avatar_url or None

    # Friendly display name
    @property
    def display_name(self) -> str:
        return self.nickname or self.phone

    # ---------- Withdrawal PIN helpers ----------
    def has_tx_pin(self) -> bool:
        return bool(self.tx_pin_hash)

    def set_tx_pin(self, raw: str) -> None:
        """
        Set/update the withdrawal password (PIN). Store a secure hash only.
        Resets attempts & lock.
        """
        self.tx_pin_hash = make_password(raw)
        self.tx_pin_changed_at = timezone.now()
        self.tx_pin_attempts = 0
        self.tx_pin_locked_until = None
        self.save(update_fields=[
            "tx_pin_hash", "tx_pin_changed_at", "tx_pin_attempts", "tx_pin_locked_until"
        ])

    def check_tx_pin(self, raw: str) -> bool:
        return bool(self.tx_pin_hash) and check_password(raw, self.tx_pin_hash)

    def can_try_tx_pin(self) -> bool:
        """Return True if user is not currently locked out."""
        return not self.tx_pin_locked_until or timezone.now() >= self.tx_pin_locked_until

    def register_tx_pin_fail(self, max_attempts: int = 5, lock_minutes: int = 10) -> None:
        """
        Increment failure counter; after `max_attempts`, lock further attempts
        for `lock_minutes`.
        """
        self.tx_pin_attempts = (self.tx_pin_attempts or 0) + 1
        if self.tx_pin_attempts >= max_attempts:
            self.tx_pin_locked_until = timezone.now() + timedelta(minutes=lock_minutes)
            self.tx_pin_attempts = 0
        self.save(update_fields=["tx_pin_attempts", "tx_pin_locked_until"])

    def register_tx_pin_success(self) -> None:
        """Reset counters after a successful PIN check."""
        if self.tx_pin_attempts or self.tx_pin_locked_until:
            self.tx_pin_attempts = 0
            self.tx_pin_locked_until = None
            self.save(update_fields=["tx_pin_attempts", "tx_pin_locked_until"])



# ---------- Dashboard Models ----------
def unique_slugify(instance, value, slug_field_name="slug", max_length=150):
    base = slugify(value)[:max_length]
    slug = base or "item"
    Model = instance.__class__
    n = 2
    while Model.objects.filter(**{slug_field_name: slug}).exists():
        slug = f"{base}-{n}"[:max_length]
        n += 1
    return slug


class Country(models.Model):
    name = models.CharField(max_length=80, unique=True)
    iso = models.CharField(max_length=2, unique=True, db_index=True)
    flag = models.CharField(max_length=8, blank=True, help_text="Emoji flag (e.g., 🇩🇪)")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.flag} {self.name}".strip()

    def save(self, *args, **kwargs):
        if self.iso:
            self.iso = self.iso.strip().upper()
        super().save(*args, **kwargs)


class Hotel(models.Model):
    # Core fields
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="hotels")
    city = models.CharField(max_length=80, blank=True)
    description_short = models.CharField("Text under it", max_length=200)

    # Image (supports file upload or URL fallback)
    cover_image = models.ImageField(upload_to="hotels/covers/", blank=True, null=True)
    cover_image_url = models.URLField(blank=True)

    # Filters
    available_date = models.DateField(blank=True, null=True)

    # Rating (numeric badge)
    score = models.DecimalField(
        max_digits=3, decimal_places=1,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="0.0 – 5.0 (one decimal)"
    )

    # Chip on card
    class Label(models.TextChoices):
        PERFECT = "perfect", "Perfect"
        GOOD = "good", "Good"
        MEDIUM = "medium", "Medium"

    label = models.CharField(
        max_length=10,
        choices=Label.choices,
        default=Label.GOOD,
        help_text="Chip on card (color-coded)"
    )

    # Tabs & ordering
    is_recommended = models.BooleanField(default=False)  # for Recommended tab pinning
    popularity = models.PositiveIntegerField(default=0)  # for Popular tab

    # Admin/moderation
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Favorites (bookmark icon)
    favorites = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="Favorite",
        related_name="favorite_hotels",
        blank=True
    )

    class Meta:
        ordering = ["-created_at", "-score", "name"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["-score"]),
            models.Index(fields=["-popularity"]),
            models.Index(fields=["is_recommended"]),
            models.Index(fields=["is_published"]),
            models.Index(fields=["country"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    @property
    def cover_src(self):
        """Return a usable image URL regardless of storage method."""
        if self.cover_image:
            try:
                return self.cover_image.url
            except ValueError:
                pass
        if self.cover_image_url:
            return self.cover_image_url
        # Default placeholder image in static files
        return "/static/img/placeholder.png"

    @property
    def favorites_count(self):
        return self.favorites.count()

    def get_absolute_url(self):
        return reverse("hotel_detail", args=[self.slug])


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "hotel")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} → {self.hotel}"



# Shared choices
class Currency(models.TextChoices):
    EUR = "EUR", "Euro (€)"
    USD = "USD", "US Dollar ($)"
    GBP = "GBP", "Pound (£)"

class AddressType(models.TextChoices):
    ETH   = "ETH", "Ethereum (ERC20)"
    TRC20 = "TRC20", "USDT (TRC20)"

class Network(models.TextChoices):
    ETH   = "ETH", "Ethereum (ERC20)"
    TRC20 = "TRC20", "USDT (TRC20)"
    
#wallet balance and bonus 

class Wallet(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet"
    )

    # Real money (cash)
    balance_cents = models.BigIntegerField(default=0)

    # Trial / bonus balance (separate from cash)
    bonus_cents = models.BigIntegerField(default=0)

    # Pending funds (e.g., in-flight deposits)
    pending_cents = models.BigIntegerField(default=0)

    # Mark when the signup trial bonus was granted (None = not yet)
    trial_bonus_at = models.DateTimeField(blank=True, null=True)

    # --- Helpers ---
    def balance(self) -> float:
        """Total balance in EUR as float (cash + bonus)."""
        return (self.balance_cents + self.bonus_cents) / 100

    @property
    def cash_eur(self) -> str:
        return f"€{self.balance_cents / 100:,.2f}"

    @property
    def bonus_eur(self) -> str:
        return f"€{self.bonus_cents / 100:,.2f}"

    @property
    def total_eur(self) -> str:
        return f"€{(self.balance_cents + self.bonus_cents) / 100:,.2f}"

    def __str__(self):
        return f"Wallet({self.user})"

    # --- Atomic balance mutations with ledger rows ---
    def credit(self, amount_cents: int, *, bucket: str = "CASH", kind: str = "ADJUST", memo: str = "", created_by=None):
        """
        Increase a balance bucket (CASH or BONUS) and write a WalletTxn.
        amount_cents must be positive.
        """
        if amount_cents <= 0:
            raise ValueError("credit() requires positive amount_cents")
        if bucket not in ("CASH", "BONUS"):
            raise ValueError("bucket must be 'CASH' or 'BONUS'")

        with transaction.atomic():
            if bucket == "CASH":
                Wallet.objects.filter(pk=self.pk).update(balance_cents=F("balance_cents") + amount_cents)
            else:
                Wallet.objects.filter(pk=self.pk).update(bonus_cents=F("bonus_cents") + amount_cents)

            WalletTxn.objects.create(
                wallet=self,
                amount_cents=amount_cents,
                kind=kind,
                bucket=bucket,
                memo=memo,
                created_by=created_by,
            )

    def debit(self, amount_cents: int, *, bucket: str = "CASH", kind: str = "ADJUST", memo: str = "", created_by=None):
        """
        Decrease a balance bucket (CASH or BONUS) and write a WalletTxn.
        amount_cents must be positive; it is stored as negative in the ledger.
        """
        if amount_cents <= 0:
            raise ValueError("debit() requires positive amount_cents")
        if bucket not in ("CASH", "BONUS"):
            raise ValueError("bucket must be 'CASH' or 'BONUS'")

        with transaction.atomic():
            if bucket == "CASH":
                Wallet.objects.filter(pk=self.pk).update(balance_cents=F("balance_cents") - amount_cents)
            else:
                Wallet.objects.filter(pk=self.pk).update(bonus_cents=F("bonus_cents") - amount_cents)

            WalletTxn.objects.create(
                wallet=self,
                amount_cents=-amount_cents,
                kind=kind,
                bucket=bucket,
                memo=memo,
                created_by=created_by,
            )


class WalletTxn(models.Model):
    """
    Ledger row for wallet movements.
    Positive amount_cents = credit, negative = debit.
    """
    KIND_CHOICES = [
        ("BONUS", "Bonus"),
        ("DEPOSIT", "Deposit"),
        ("WITHDRAW", "Withdraw"),
        ("ADJUST", "Adjust"),
    ]
    BUCKET_CHOICES = [
        ("CASH", "Cash"),
        ("BONUS", "Bonus"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="txns")
    amount_cents = models.BigIntegerField(default=0)  # no nulls; defaults to 0
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    bucket = models.CharField(max_length=10, choices=BUCKET_CHOICES, default="CASH")  # which balance was touched
    memo = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["wallet", "-created_at"]),
        ]

    @property
    def amount_eur(self) -> str:
        sign = "-" if self.amount_cents < 0 else ""
        return f"{sign}€{abs(self.amount_cents) / 100:,.2f}"

    def __str__(self):
        sign = "+" if self.amount_cents >= 0 else "-"
        return f"{self.wallet.user} {self.kind}/{self.bucket} {sign}€{abs(self.amount_cents)/100:.2f}"






#pay address add
class AddressType(models.TextChoices):
    ETH = 'ETH', 'Ethereum (ERC-20)'
    TRC20 = 'TRC20', 'USDT (TRC-20)'

# Saved payout addresses (for withdrawals)
class PayoutAddress(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payout_addresses"
    )
    label = models.CharField(max_length=64, blank=True)
    address_type = models.CharField(max_length=10, choices=AddressType.choices)
    address = models.CharField(max_length=128)
    is_verified = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Enforce ONE address per network per user (prevents "second time" crash pattern)
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'address_type'],
                name='uniq_user_network_address'
            ),
        ]
        # Helpful indexes for lookups
        indexes = [
            models.Index(fields=['user', 'address_type']),
        ]
        # Remove legacy unique_together if it exists in your code/migrations
        # unique_together = (('user', 'address_type', 'address'),)

    def __str__(self):
        return f'{self.user} — {self.address_type} — {self.label or self.address[:8]}…'

    def normalize(self):
        """Normalize address casing/whitespace before save."""
        if not self.address:
            return
        self.address = self.address.strip()
        # Keep 0x prefix; normalize hex to lowercase for ETH
        if self.address_type == AddressType.ETH and self.address.startswith('0x') and len(self.address) == 42:
            self.address = '0x' + self.address[2:].lower()

    def save(self, *args, **kwargs):
        self.normalize()
        super().save(*args, **kwargs)



"""
# Withdrawal requests
class WithdrawalRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="withdrawals")
    amount_cents = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.EUR)
    address = models.ForeignKey(PayoutAddress, on_delete=models.PROTECT)
    fee_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, default="pending")  # pending/success/failed
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def amount(self): return self.amount_cents / 100
    @property
    def fee(self): return self.fee_cents / 100
"""

class WithdrawalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    FAILED = "failed", "Failed"


class WithdrawalRequest(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="withdrawals"
    )
    amount_cents = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.EUR
    )
    address = models.ForeignKey(PayoutAddress, on_delete=models.PROTECT)
    fee_cents = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=WithdrawalStatus.choices,
        default=WithdrawalStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)

    @property
    def amount(self):
        return self.amount_cents / 100

    @property
    def fee(self):
        return self.fee_cents / 100

    def mark_as_confirmed(self):
        """Mark withdrawal as confirmed and set the confirmation timestamp."""
        self.status = WithdrawalStatus.CONFIRMED
        self.confirmed_at = timezone.now()
        self.save()

    def __str__(self):
        return f"{self.user} - {self.amount} {self.currency} ({self.status})"

# Admin-managed receiving address for deposits
class DepositAddress(models.Model):
    network = models.CharField(max_length=10, choices=Network.choices, unique=True)
    address = models.CharField(max_length=128)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_network_display()} • {self.address[:8]}…"

class DepositStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AWAITING_PAYMENT = "awaiting_payment", "Awaiting Payment"
    AWAITING_REVIEW = "awaiting_review", "Awaiting Review"
    CONFIRMED = "confirmed", "Confirmed"
    FAILED = "failed", "Failed"

class DepositRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deposits")
    amount_cents = models.PositiveIntegerField()
    txid = models.CharField(max_length=128, blank=True, null=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.EUR)
    network = models.CharField(max_length=10, choices=Network.choices)
    pay_to = models.ForeignKey(DepositAddress, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=DepositStatus.choices, default=DepositStatus.AWAITING_PAYMENT)
    reference = models.CharField(max_length=20, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    @property
    def amount(self): return self.amount_cents / 100
    @staticmethod
    def new_reference(): return get_random_string(12).upper()


class InfoPage(models.Model):
    class Key(models.TextChoices):
        ABOUT = "about", _("About us")
        CONTACT = "contact", _("Contact us")
        HELP = "help", _("Help")
        LEVEL = "level", _("Level")
        SIGNIN_REWARD = "signin_reward", _("Sign-in reward")

    key = models.CharField(max_length=32, choices=Key.choices, unique=True)
    title = models.CharField(max_length=120)
    body = models.TextField(blank=True)  # write your copy here
    is_published = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key",)

    def __str__(self):
        return self.get_key_display()


class AnnouncementQuerySet(models.QuerySet):
    def active(self):
        now = timezone.now()
        return self.filter(
            is_published=True
        ).filter(
            models.Q(starts_at__lte=now) | models.Q(starts_at__isnull=True)
        ).filter(
            models.Q(ends_at__gte=now) | models.Q(ends_at__isnull=True)
        )

class Announcement(models.Model):
    title = models.CharField(max_length=140)
    body = models.TextField()
    pinned = models.BooleanField(default=False)   # show first
    is_published = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AnnouncementQuerySet.as_manager()

    class Meta:
        ordering = ("-pinned", "-created_at")

    def __str__(self):
        return self.title

# for task settings
class SystemSettings(models.Model):
    """
    Admin-configurable policy. Only the 'trial_max_tasks' business rule is
    fixed in logic (25), even if this field is changed manually.
    """
    # ---- Trial ----
    trial_task_cost_cents = models.PositiveIntegerField(default=1200)  # €12.00
    trial_commission_cents = models.PositiveIntegerField(default=145)  # €1.45
    trial_max_tasks = models.PositiveIntegerField(default=25)          # business rule: enforce as 25

    # ---- Normal ----
    normal_task_limit = models.PositiveIntegerField(default=3)         # tasks before VIP
    normal_commission_cents = models.PositiveIntegerField(default=200) # e.g., €2.00
    normal_worth_cents = models.PositiveIntegerField(default=0)        # default 0 (can enable later)

    # ---- VIP defaults (optional) ----
    vip_default_commission_cents = models.PositiveIntegerField(default=300)  # e.g., €3.00

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System Settings"
        verbose_name_plural = "System Settings"

    def __str__(self):
        return "System Settings"

    @classmethod
    def current(cls) -> "SystemSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        # Enforce fixed business rule regardless of DB value
        if obj.trial_max_tasks != 25:
            obj.trial_max_tasks = 25
            obj.save(update_fields=["trial_max_tasks"])
        return obj

#task phase

class TaskPhase(models.TextChoices):
    TRIAL = "TRIAL", "Trial"
    NORMAL = "NORMAL", "Normal"
    VIP = "VIP", "VIP"

class TaskStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"          # created/assigned
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    SUBMITTED = "SUBMITTED", "Submitted"    # user says it's done
    APPROVED = "APPROVED", "Approved"       # admin/system verifies
    REJECTED = "REJECTED", "Rejected"
    CANCELED = "CANCELED", "Canceled"

class Task(models.Model):
    """
    Canonical record for any user task across phases.
    Monetary values are in cents for precision; no ledger is used.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks"
    )
    phase = models.CharField(max_length=10, choices=TaskPhase.choices, db_index=True)

    # Sequential number within the phase (1..25 for Trial; 1..normal_limit for Normal; 1.. for VIP)
    index_in_phase = models.PositiveIntegerField()

    # Money (all in cents)
    cost_cents = models.IntegerField(default=0)        # e.g., Trial cost: 1200; Normal default 0
    commission_cents = models.IntegerField(default=0)  # reward for this task
    worth_cents = models.IntegerField(default=0)       # VIP worth; Normal default 0

    # VIP inputs (optional)
    deposit_required_cents = models.PositiveIntegerField(default=0)

    #for template
    template = models.ForeignKey("TaskTemplate", null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="tasks")
    # Lifecycle & audit
    status = models.CharField(max_length=20, choices=TaskStatus.choices, default=TaskStatus.PENDING, db_index=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tasks"
    )
    idempotency_key = models.CharField(max_length=50, unique=True, help_text="Prevents double-processing")

    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        unique_together = [("user", "phase", "index_in_phase")]
        indexes = [models.Index(fields=["user", "phase", "status"])]

    def __str__(self):
        return f"{self.user} • {self.phase} #{self.index_in_phase} • {self.status}"

    # Convenience
    @property
    def is_trial(self): return self.phase == TaskPhase.TRIAL
    @property
    def is_normal(self): return self.phase == TaskPhase.NORMAL
    @property
    def is_vip(self): return self.phase == TaskPhase.VIP






class TaskTemplate(models.Model):
    # Which phase this template is for
    phase = models.CharField(
        max_length=10,
        choices=TaskPhase.choices,   # same enum as Task
        db_index=True,
        default=TaskPhase.TRIAL,
    )

    # Core fields
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True, blank=True)

    # Location as simple text (no FK)
    country = models.CharField(max_length=160, db_index=True)
    city = models.CharField(max_length=80, blank=True)

    # Image (file or URL fallback)
    cover_image = models.ImageField(upload_to="tasks/covers/%Y/%m/", blank=True, null=True)
    cover_image_url = models.URLField(blank=True)

    # Order info
    order_code = models.CharField("Order ID", max_length=64, unique=True, db_index=True)
    order_date = models.DateField(blank=True, null=True)

    # Money in cents
    worth_cents = models.PositiveIntegerField(default=0, help_text="€ in cents, e.g. 15000 = €150.00")
    commission_cents = models.PositiveIntegerField(default=0, help_text="€ in cents, e.g. 350 = €3.50")

    # Rating badge (numeric)
    score = models.DecimalField(
        max_digits=3, decimal_places=1,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="0.0 – 5.0 (one decimal)"
    )

    # Chip label
    class Label(models.TextChoices):
        PERFECT = "perfect", "Perfect"
        GOOD = "good", "Good"
        MEDIUM = "medium", "Medium"

    label = models.CharField(
        max_length=10,
        choices=Label.choices,
        default=Label.GOOD,
        help_text="Chip on card (color-coded)"
    )

    # Publishing flags
    is_published = models.BooleanField(default=True)
    is_recommended = models.BooleanField(default=False)
    popularity = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-score", "name"]
        indexes = [
            models.Index(fields=["phase", "is_published"]),
            models.Index(fields=["-created_at"]),
            models.Index(fields=["-score"]),
            models.Index(fields=["-popularity"]),
            models.Index(fields=["country"]),
        ]

    def __str__(self):
        return f"[{self.phase}] {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    @property
    def cover_src(self):
        if self.cover_image:
            try:
                return self.cover_image.url
            except ValueError:
                pass
        if self.cover_image_url:
            return self.cover_image_url
        return "/static/img/placeholder.png"

    @property
    def worth_eur(self) -> str:
        return f"€{self.worth_cents/100:,.2f}"

    @property
    def commission_eur(self) -> str:
        return f"€{self.commission_cents/100:,.2f}"


# models.py (append)
from django.db import models
from django.conf import settings
from django.utils import timezone

class AccountDisplayMode(models.TextChoices):
    AUTO = "AUTO", "Auto (computed)"
    MANUAL = "MANUAL", "Manual (admin override)"

class AccountDisplay(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_display"
    )
    # Which source to use for display
    mode = models.CharField(
        max_length=10,
        choices=AccountDisplayMode.choices,
        default=AccountDisplayMode.AUTO,
        db_index=True,
    )

    # Stored amounts in cents (editable in admin)
    total_assets_cents = models.BigIntegerField(default=0)
    asset_cents        = models.BigIntegerField(default=0)
    dividends_cents    = models.BigIntegerField(default=0)
    processing_cents   = models.BigIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["mode"]),
        ]
        verbose_name = "Account Display"
        verbose_name_plural = "Account Displays"

    def __str__(self):
        return f"AccountDisplay({self.user}) — {self.mode}"

    # Convenience formatters
    @property
    def total_assets_eur(self): return f"€{self.total_assets_cents/100:,.2f}"
    @property
    def asset_eur(self):        return f"€{self.asset_cents/100:,.2f}"
    @property
    def dividends_eur(self):    return f"€{self.dividends_cents/100:,.2f}"
    @property
    def processing_eur(self):   return f"€{self.processing_cents/100:,.2f}"




from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_account_display(sender, instance, created, **kwargs):
    """
    Make sure every user has an AccountDisplay row.
    Safe to call repeatedly; get_or_create prevents duplicates.
    """
    from .models import AccountDisplay  # local import avoids circulars during app registry
    if not created:
        # If you only want to create on first signup, you can return here.
        # But get_or_create is cheap; leaving it makes it robust.
        pass
    AccountDisplay.objects.get_or_create(user=instance)
