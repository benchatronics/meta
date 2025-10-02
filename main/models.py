# models.py
from __future__ import annotations
from decimal import Decimal
import uuid
import random
from django.db.models import Q
from django.core.exceptions import ValidationError
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
from .task_currency import to_cents
from datetime import timedelta
from django.contrib.auth.hashers import make_password, check_password

from django.http import Http404

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.contrib.auth import get_user_model

User = settings.AUTH_USER_MODEL

class InvitationLink(models.Model):
    """
    A single-use invitation code (typed by the user).
    Admins create these in the admin. Codes can be suspended/expired.
    """
    code = models.CharField(max_length=32, unique=True, db_index=True)
    owner = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="invites_created"
    )
    label = models.CharField(max_length=120, blank=True)

    # Lifecycle
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Single-use control
    claimed = models.BooleanField(default=False)  # flipped atomically
    used_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="invite_used"
    )
    used_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "ACTIVE"
        if not self.is_active:
            status = "SUSPENDED"
        if self.is_expired:
            status = "EXPIRED"
        if self.used_by_id:
            status = f"USED by {self.used_by_id}"
        return f"{self.code} [{status}]"

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)

    @property
    def is_valid_now(self) -> bool:
        return self.is_active and not self.is_expired and not self.used_by_id and not self.claimed

    @staticmethod
    def generate_code(length: int = 12) -> str:
        # No confusing chars like I/O/0/1
        return get_random_string(length=length, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789")

    @classmethod
    def can_be_used(cls, code: str) -> bool:
        now = timezone.now()
        return cls.objects.filter(
            code__iexact=code,
            is_active=True,
            claimed=False,
            used_by__isnull=True,
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exists()



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


def flag_from_iso2(code: str) -> str:
    code = (code or "").strip().upper()
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code if "A" <= c <= "Z")

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
            # 👉 Only set flag if it's empty, so manual overrides still work
            if not self.flag:
                self.flag = flag_from_iso2(self.iso)
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

    # -----------------------
    # Idempotent new helpers
    # -----------------------
    def credit_once(self, amount_cents, *, bucket="CASH", kind="ADJUST", memo="", external_ref=None, created_by=None) -> bool:
        """
        Idempotent credit:
          - If external_ref is provided and already exists for this wallet, do nothing (return False).
          - Else increment balance and insert a ledger row (return True).
        """
        if amount_cents <= 0:
            raise ValueError("credit_once() requires positive amount_cents")
        if bucket not in ("CASH", "BONUS"):
            raise ValueError("bucket must be 'CASH' or 'BONUS'")

        with transaction.atomic():
            if external_ref and WalletTxn.objects.filter(wallet=self, external_ref=external_ref).exists():
                return False

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
                external_ref=external_ref or "",
                created_by=created_by,
            )
            return True

    def debit_once(self, amount_cents, *, bucket="CASH", kind="ADJUST", memo="", external_ref=None, created_by=None) -> bool:
        """
        Idempotent debit (stored as negative in ledger):
          - If external_ref exists for this wallet, do nothing (return False).
          - Else decrement balance and insert a ledger row (return True).
        """
        if amount_cents <= 0:
            raise ValueError("debit_once() requires positive amount_cents")
        if bucket not in ("CASH", "BONUS"):
            raise ValueError("bucket must be 'CASH' or 'BONUS'")

        with transaction.atomic():
            if external_ref and WalletTxn.objects.filter(wallet=self, external_ref=external_ref).exists():
                return False

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
                external_ref=external_ref or "",
                created_by=created_by,
            )
            return True

    # -----------------------------
    # Your original, unchanged APIs
    # -----------------------------
    def credit(self, amount_cents: int, *, bucket: str = "CASH", kind: str = "ADJUST", memo: str = "", created_by=None):
        """
        Non-idempotent credit (kept exactly as before). Prefer credit_once() for payouts/deposits you must not duplicate.
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
                external_ref="",   # legacy
                created_by=created_by,
            )

    def debit(self, amount_cents: int, *, bucket: str = "CASH", kind: str = "ADJUST", memo: str = "", created_by=None):
        """
        Non-idempotent debit (kept exactly as before). Prefer debit_once() for debits you must not duplicate.
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
                external_ref="",   # legacy
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
    amount_cents = models.BigIntegerField(default=0)  # positive for credits, negative for debits
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    bucket = models.CharField(max_length=10, choices=BUCKET_CHOICES, default="CASH")
    memo = models.CharField(max_length=255, blank=True)

    # NEW: idempotency key (blank allowed for legacy). Use per wallet + business event.
    external_ref = models.CharField(max_length=64, blank=True, default="", db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["wallet", "-created_at"]),
            models.Index(fields=["wallet", "external_ref"]),
        ]
        constraints = [
            # Enforce uniqueness for non-blank external_ref per wallet (allows many blank legacy rows)
            models.UniqueConstraint(
                fields=["wallet", "external_ref"],
                name="uniq_wallet_external_ref",
                condition=~models.Q(external_ref="")
            )
        ]

    @property
    def amount_eur(self) -> str:
        sign = "-" if self.amount_cents < 0 else ""
        return f"{sign}€{abs(self.amount_cents) / 100:,.2f}"

    def __str__(self):
        sign = "+" if self.amount_cents >= 0 else "-"
        return f"{self.wallet.user} {self.kind}/{self.bucket} {sign}€{abs(self.amount_cents)/100:.2f}"


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
        """
        Mark withdrawal as confirmed and notify task progress so Asset logic
        stays consistent after withdrawals.
        """
        # 1) Persist confirmation
        self.status = WithdrawalStatus.CONFIRMED
        self.confirmed_at = timezone.now()
        self.save(update_fields=["status", "confirmed_at"])

        # 2) Tell UserTaskProgress a withdrawal was completed so it can:
        #    - advance dividends_paid_cents by the withdrawn amount
        #    - pin the withdraw cycle (via mark_withdraw_done inside)
        # This is safe to import here to avoid circular imports.
        from .models import ensure_task_progress

        prog = ensure_task_progress(self.user)
        amt = int(self.amount_cents or 0)
        if amt > 0 and hasattr(prog, "on_withdraw_confirmed"):
            prog.on_withdraw_confirmed(amt)

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


#Task settings

# ---- Singleton base so we always have exactly one row (pk=1) ----
class _SingletonModel(models.Model):
    class Meta:
        abstract = True

    cycles_between_withdrawals = models.PositiveIntegerField(
    default=2, validators=[MinValueValidator(1)],
    help_text="How many full cycles must be completed between withdrawals."
    )

    def save(self, *args, **kwargs):
        # enforce single row at pk=1
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class tasksettngs(_SingletonModel):
    """
    Global knobs for your task engine (single row).
    Includes a toggle to remove the user's trial bonus at the cycle limit.
    """

    # --- Task cycle controls ---
    task_limit_per_cycle = models.PositiveIntegerField(
        default=25,
        validators=[MinValueValidator(1)],
        help_text="Number of tasks allowed in a cycle before the user is blocked."
    )
    block_on_reaching_limit = models.BooleanField(
        default=True,
        help_text="If enabled, the user becomes blocked immediately upon reaching the cycle limit."
    )
    block_message = models.CharField(
        max_length=255,
        default="Trial limit reached. Please contact customer care to continue.",
        help_text="Shown to the user when blocked."
    )

    # --- Per-task amounts ---
    task_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('12.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Optional per-task charge (set 0.00 if not used)."
    )
    task_commission = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('1.45'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Commission paid to the user per completed task."
    )

    # --- Trial bonus behavior at limit (NEW) ---
    clear_trial_bonus_at_limit = models.BooleanField(
        default=True,
        help_text="If enabled, the user's trial bonus will be cleared when they hit the cycle limit."
    )

    # --- housekeeping ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Task Settings"
        verbose_name_plural = "Task Settings"

    def __str__(self):
        return "Task Settings"


# =======================
# Per-user task instances
# =======================
# ====== helpers (idempotent wallet ops) ======
def _wallet_credit_idem(wallet, amount_cents: int, *, memo: str, bucket="CASH", kind="ADJUST", external_ref: str = ""):
    if amount_cents <= 0:
        return False
    if hasattr(wallet, "credit_once"):
        return wallet.credit_once(
            amount_cents,
            bucket=bucket,
            kind=kind,
            memo=memo,
            external_ref=external_ref or "",
        )
    wallet.credit(amount_cents, bucket=bucket, kind=kind, memo=memo)
    return True

def _wallet_debit_idem(wallet, amount_cents: int, *, memo: str, bucket="CASH", kind="ADJUST", external_ref: str = ""):
    if amount_cents <= 0:
        return False
    if hasattr(wallet, "debit_once"):
        return wallet.debit_once(
            amount_cents,
            bucket=bucket,
            kind=kind,
            memo=memo,
            external_ref=external_ref or "",
        )
    wallet.debit(amount_cents, bucket=bucket, kind=kind, memo=memo)
    return True



class UserTask(models.Model):
    class Status(models.TextChoices):
        PENDING     = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        SUBMITTED   = "SUBMITTED", "Submitted"
        APPROVED    = "APPROVED", "Approved"
        REJECTED    = "REJECTED", "Rejected"
        CANCELED    = "CANCELED", "Canceled"

    class Kind(models.TextChoices):
        REGULAR = "REGULAR", "Regular"
        ADMIN   = "ADMIN", "Admin (requires solvency)"

    # Links
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_tasks")
    template = models.ForeignKey('UserTaskTemplate', on_delete=models.PROTECT, related_name="instances")

    # Cycle/order context
    cycle_number = models.PositiveIntegerField(default=0)
    order_shown  = models.PositiveIntegerField(help_text="1-based order shown to the user within the cycle.")

    # State
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_PROGRESS)

    # Economics snapshot (EUR)
    from django.core.validators import MinValueValidator
    price_used = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    commission_used = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    # Kind snapshot
    task_kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.REGULAR)

    # ADMIN snapshots
    assignment_total_display_cents = models.BigIntegerField(default=0)  # user's Total Asset (display) when assigned
    required_cash_cents            = models.BigIntegerField(default=0)  # max(0, price - old total_display)

    # Optional proof payload (not shown in UI)
    proof_text = models.TextField(blank=True, default="")
    proof_link = models.URLField(blank=True, default="")

    # Optional finance hold reference
    hold_ref = models.CharField(max_length=64, blank=True, default="")

    # Timestamps
    updated_at   = models.DateTimeField(auto_now=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    started_at   = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "cycle_number"]),
            models.Index(fields=["status"]),
            models.Index(fields=["task_kind"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"UserTask#{self.pk} u={self.user} ord={self.order_shown} ({self.status})"

    # ---------- Actions ----------

    def submit(self, *, proof_text: str = "", proof_link: str = ""):
        """
        Submit this task.
        REGULAR/TRIAL: auto-approve immediately.
        ADMIN: auto-approve only if wallet CASH >= PRICE (strict solvency). We DO NOT debit the price.
        """
        if self.status != self.Status.IN_PROGRESS:
            raise ValidationError("Task cannot be submitted in its current state.")

        self.proof_text = proof_text or ""
        self.proof_link = proof_link or ""

        if self.task_kind == self.Kind.ADMIN:
            price_cents = to_cents(self.price_used)

            # Strict solvency on CASH (bonus doesn’t count here)
            wallet = self.user.wallet
            if (wallet.balance_cents or 0) < price_cents:
                raise ValidationError("Insufficient funds — please deposit the task price and try again.")

            # Auto-approve inline (idempotent + locked). We do NOT debit price.
            self._auto_approve_admin_inline()
            return

        # Regular / trial → immediate path
        self._auto_approve_regular()

    def _auto_approve_regular(self):
        """
        Finalize a regular/trial task:
          - Add commission to dividends
          - Credit wallet with THIS commission now (idempotent)
          - Mark this commission as 'paid' so admin approvals won't re-pay it
          - Dashboard: normal
          - Task -> APPROVED, then advance
        """
        from .models import ensure_task_progress  # local import to avoid circulars
        prog = ensure_task_progress(self.user)
        commission_cents = to_cents(self.commission_used)
        wallet = self.user.wallet

        with transaction.atomic():
            prog.add_commission(commission_cents)

            if commission_cents > 0:
                _wallet_credit_idem(
                    wallet,
                    int(commission_cents),
                    memo=f"REGULAR_TASK_PAYOUT #{self.pk}",
                    external_ref=f"REGULAR_TASK_PAYOUT#{self.pk}",
                )
                # mark paid (clamped)
                prev_paid = int(getattr(prog, "dividends_paid_cents", 0) or 0)
                new_paid  = prev_paid + int(commission_cents)
                max_pay   = int(prog.dividends_cents or 0)
                prog.dividends_paid_cents = max(0, min(new_paid, max_pay))
                prog.save(update_fields=["dividends_paid_cents", "updated_at"])

            prog.set_state_normal()

            now = timezone.now()
            self.status = self.Status.APPROVED
            self.submitted_at = now
            self.decided_at = now
            self.save(update_fields=["proof_text", "proof_link", "status", "submitted_at", "decided_at", "updated_at"])

            prog.advance()

    def _auto_approve_admin_inline(self):
        """
        Auto-approve ADMIN after strict solvency:
          - Lock to prevent double-run; exit if already APPROVED.
          - DO NOT debit price (wallet remains whole).
          - Credit wallet: unpaid_old_dividends + THIS admin commission (idempotent).
          - Add THIS admin commission to dividends; mark all dividends PAID.
          - Dashboard: set settled; approve task; advance.
        """
        from .models import ensure_task_progress  # local import to avoid circulars
        price_cents = to_cents(self.price_used)
        admin_commission_cents = to_cents(self.commission_used)
        wallet = self.user.wallet
        prog = ensure_task_progress(self.user)

        with transaction.atomic():
            # Lock & re-check status to avoid duplicate approvals
            locked = (type(self).objects
                      .select_for_update()
                      .only("id", "status")
                      .get(pk=self.pk))
            if locked.status == self.Status.APPROVED:
                return

            # 1) Unpaid old dividends BEFORE adding this admin commission (clamped)
            div_cents  = int(prog.dividends_cents or 0)
            paid_cents = int(getattr(prog, "dividends_paid_cents", 0) or 0)
            paid_cents = max(0, min(paid_cents, div_cents))
            unpaid_old = div_cents - paid_cents

            # 2) Credit payout = unpaid_old + admin_commission (NEVER price)
            payout_cents = int(unpaid_old) + int(admin_commission_cents)
            if payout_cents > 0:
                _wallet_credit_idem(
                    wallet,
                    payout_cents,
                    memo=f"ADMIN_TASK_PAYOUT #{self.pk}",
                    external_ref=f"ADMIN_TASK_PAYOUT#{self.pk}",
                )

            # 3) Dividends → add this admin commission & mark ALL dividends paid
            prog.add_commission(admin_commission_cents)
            prog.dividends_paid_cents = int(prog.dividends_cents or 0)
            prog.save(update_fields=["dividends_paid_cents", "updated_at"])

            # 4) Dashboard cache (legacy; final display uses display_totals rules)
            prog.set_state_admin_approved(price_cents=price_cents)

            # 5) Approve + advance
            now = timezone.now()
            self.status = self.Status.APPROVED
            self.submitted_at = now
            self.decided_at = now
            self.save(update_fields=["status", "submitted_at", "decided_at", "updated_at"])

            prog.advance()

    # PATCH: compute REQUIRED using CASH ONLY so you don’t need to deposit twice
    # (keeps the rest of your logic exactly as-is)

    def mark_admin_assigned_effects(self):
        """
        Call ONCE when this ADMIN task is assigned.

        Pre-approval dashboard:
          - asset = -REQUIRED (negative; what the user must deposit)
          - processing = price + new_admin_commission
          - total mirrors asset during assignment (handled when processing > 0)
          - Snapshot 'old_total_display' (equals wallet) and 'required' here; do NOT recompute later.
        """
        if self.task_kind != self.Kind.ADMIN:
            return

        from .models import ensure_task_progress  # local import to avoid circulars
        prog = ensure_task_progress(self.user)
        price_cents = to_cents(self.price_used)
        admin_commission_cents = to_cents(self.commission_used)

        totals = prog.display_totals
        old_total_display = int(totals.get("total_asset_cents", 0))  # equals wallet in settled state
        required = max(0, price_cents - old_total_display)

        self.assignment_total_display_cents = old_total_display
        self.required_cash_cents = required
        self.save(update_fields=["assignment_total_display_cents", "required_cash_cents", "updated_at"])

        prog.set_state_admin_assigned(
            price_cents=price_cents,
            admin_commission_cents=admin_commission_cents,
            required_cents=required,
        )

    def approve_admin(self, *, approved_by=None):
        """Manual path if SUBMITTED — finalize inline using the same rules (no price debit)."""
        if self.task_kind != self.Kind.ADMIN:
            raise ValidationError("Not an admin-priced task.")
        if self.status == self.Status.APPROVED:
            raise ValidationError("Task already approved.")
        if self.status != self.Status.SUBMITTED:
            raise ValidationError("Only submitted admin tasks can be approved manually.")
        self._auto_approve_admin_inline()

    def approve_regular(self, *, approved_by=None):
        """Regular/trial tasks auto-complete on submit; no manual approval needed."""
        raise ValidationError("Regular/trial tasks auto-complete on submit; no manual approval needed.")


# ======================================================
# Admin forcing multiple tasks per cycle (queued rules)
# ======================================================
class ForcedTaskDirective(models.Model):
    class Status(models.TextChoices):
        PENDING  = "PENDING", "Pending"
        CONSUMED = "CONSUMED", "Consumed"
        CANCELED = "CANCELED", "Canceled"
        EXPIRED  = "EXPIRED", "Expired"
        SKIPPED  = "SKIPPED", "Skipped (behind current order)"

    # use custom user model
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="forced_task_directives")
    applies_on_cycle = models.PositiveIntegerField(db_index=True, help_text="Cycle number this applies to.")
    target_order = models.PositiveIntegerField(help_text="1-based order to show when eligible.")

    # optional fixed template to serve at this order
    template = models.ForeignKey(
        'UserTaskTemplate', null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Optional fixed template to serve for this order."
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    expires_at = models.DateTimeField(null=True, blank=True)
    batch_id = models.CharField(max_length=64, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")

    # who created the directive (also custom user)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_forced_task_directives"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    skipped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        #two verbose name
        verbose_name = "Special order"
        verbose_name_plural = "Special orders"

        indexes = [
            models.Index(fields=["user", "applies_on_cycle", "status"]),
            models.Index(fields=["applies_on_cycle", "target_order"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["target_order", "created_at"]

    def __str__(self):
        return f"Force u={self.user} cyc={self.applies_on_cycle} → {self.target_order} [{self.status}]"

#UserTaskTemplate
def _gen_task_id():
    """Short, unique, URL-safe id for reference."""
    return uuid.uuid4().hex[:12].upper()

def _unique_slug(base: str, qs, max_len: int = 180):
    """Create a unique slug within qs from base."""
    base = (slugify(base) or "task")[:max_len]
    slug = base
    i = 2
    while qs.filter(slug=slug).exists():
        suffix = f"-{i}"
        slug = f"{base[:max_len - len(suffix)]}{suffix}"
        i += 1
    return slug

#user tasK template
class UserTaskTemplate(models.Model):
    # ---- Labels / rating ----
    class Label(models.TextChoices):
        PERFECT = "PERFECT", "Perfect"
        GOOD    = "GOOD",    "Good"
        MEDIUM  = "MEDIUM",  "Medium"

    # ---- Publish state + admin flag ----
    class Status(models.TextChoices):
        DRAFT    = "DRAFT", "Draft"
        ACTIVE   = "ACTIVE", "Active"
        PAUSED   = "PAUSED", "Paused"
        ARCHIVED = "ARCHIVED", "Archived"

    # ---- Fields ----
    hotel_name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)

    country = models.CharField(max_length=64)
    city = models.CharField(max_length=64)

    cover_image_url = models.URLField(blank=True, default="")
    cover_image = models.ImageField(upload_to="tasks/covers/", null=True, blank=True)

    task_id = models.CharField(max_length=24, unique=True, default=_gen_task_id, editable=False)
    task_date = models.DateField(null=True, blank=True)

    task_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="If empty, falls back to tasksettngs.task_price."
    )
    task_commission = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="If empty, falls back to tasksettngs.task_commission."
    )

    task_score = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("5.00"))],
        help_text="0.00 – 5.00"
    )
    task_label = models.CharField(max_length=12, choices=Label.choices, blank=True, default="")

    is_admin_task = models.BooleanField(
        default=False,
        help_text="If True, user must have wallet ≥ price to complete this task."
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT,
        help_text="Set to ACTIVE to include in random selection."
    )

    # Audit
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_task_templates"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["country", "city"]),
            models.Index(fields=["task_date"]),
        ]
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        return f"{self.hotel_name} ({self.city}, {self.country})"

    def save(self, *args, **kwargs):
        if not self.slug and self.hotel_name:
            self.slug = _unique_slug(self.hotel_name, self.__class__.objects.all(), max_len=180)
        super().save(*args, **kwargs)

    # ---- Helpers ----
    def effective_price(self) -> Decimal:
        if self.task_price is not None:
            return self.task_price
        from .models import tasksettngs
        return tasksettngs.load().task_price

    def effective_commission(self) -> Decimal:
        if self.task_commission is not None:
            return self.task_commission
        from .models import tasksettngs
        return tasksettngs.load().task_commission

    def is_active_now(self) -> bool:
        return self.status == self.Status.ACTIVE

# =======================
# User task progress
# =======================

class UserTaskProgress(models.Model):
    """
    Tracks per-user progress within the current cycle and snapshots
    the per-cycle economics from tasksettngs at cycle start.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_progress",
    )

    # cycle/progress
    cycles_completed = models.PositiveIntegerField(default=0)
    current_task_index = models.PositiveIntegerField(
        default=0, help_text="0-based; next visible = index + 1"
    )
    is_blocked = models.BooleanField(default=False, db_index=True)

    # snapshots copied from tasksettngs when a new cycle starts
    limit_snapshot = models.PositiveIntegerField(default=25)
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("12.00"))
    commission_snapshot = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("1.45"))

    # Dashboard counters (all in cents)
    dividends_cents       = models.BigIntegerField(default=0)  # all commissions: trial + regular + admin
    dividends_paid_cents  = models.BigIntegerField(default=0)  # how much of dividends has been cashed out
    asset_cents           = models.BigIntegerField(default=0)  # cache used during assignment (negative required) / legacy
    processing_cents      = models.BigIntegerField(default=0)  # temporary while an admin task is assigned

    #tracking user bonus date
    first_reward_date = models.DateField(null=True, blank=True)

    # remember which cycle the last withdrawal happened
    last_withdraw_cycle   = models.PositiveIntegerField(default=0)

    last_reset_at = models.DateTimeField(null=True, blank=True)
    updated_at    = models.DateTimeField(auto_now=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["is_blocked"]),
        ]

    def __str__(self):
        return f"{self.user} | {self.current_task_index}/{self.limit_snapshot} blocked={self.is_blocked}"

    @property
    def natural_next_order(self) -> int:
        return self.current_task_index + 1

    # ---------- Dashboard states ----------

    def set_state_normal(self, *, preserve_settled: bool = True):
        """
        Trial/regular settled:
          Asset = Dividends; Processing = 0.
        Do not overwrite settled admin-approved cache (asset > dividends and processing == 0).
        """
        if preserve_settled and (self.processing_cents or 0) == 0:
            if (self.asset_cents or 0) > (self.dividends_cents or 0):
                return
        self.asset_cents = self.dividends_cents or 0
        self.processing_cents = 0
        self.save(update_fields=["asset_cents", "processing_cents", "updated_at"])

    def set_state_admin_assigned(self, *, price_cents: int, admin_commission_cents: int, required_cents: int):
        """
        PRE-APPROVAL (assigned):
          - Do NOT change dividends
          - Asset = -REQUIRED CASH
          - Processing = price + new admin commission
          - Total (display) mirrors Asset during this phase (see display_totals)
        """
        with transaction.atomic():
            self.asset_cents = -int(required_cents)
            self.processing_cents = int(price_cents) + int(admin_commission_cents)
            self.save(update_fields=["asset_cents", "processing_cents", "updated_at"])

    def set_state_admin_approved(self, *, price_cents: int):
        """
        AFTER admin approval:
          Cache asset = price for legacy; final display computes fresh every time.
        """
        self.asset_cents = int(price_cents)
        self.processing_cents = 0
        self.save(update_fields=["asset_cents", "processing_cents", "updated_at"])

    # ---------- Display helper for UI ----------
    @property
    def display_totals(self) -> dict:
        """
        DISPLAY RULES (cents):

        • Admin assigned (processing > 0):
             Asset   = -required
             Total   = Asset (mirror while processing)
             Proc    = price + new admin commission
             Div     = min(paid_dividends, wallet_total)

        • Settled (processing == 0):
             TOTAL ASSET (display) = WALLET (cash + bonus)  ← ALWAYS equal
             If ANY approved ADMIN exists:
                 Asset     = Σ(required_cash_cents) across ALL approved ADMIN tasks (ever)
                              (capped to Total so it never exceeds wallet)
                 Dividends = full (unchanged)
             Else:
                 Asset     = Total - min(paid_dividends, Total)
                 Dividends = min(paid_dividends, Total)
        """
        w = getattr(self.user, "wallet", None)

        base_proc  = int(self.processing_cents or 0)
        base_div   = int(self.dividends_cents or 0)
        paid_div   = int(getattr(self, "dividends_paid_cents", 0) or 0)
        paid_div   = max(0, min(paid_div, base_div))

        # Wallet (withdrawable) — single source of truth for TOTAL when settled
        wallet_cash = wallet_bonus = 0
        if w is not None:
            try:
                wallet_cash  = int(getattr(w, "balance_cents", 0) or 0)
                wallet_bonus = int(getattr(w, "bonus_cents", 0) or 0)
            except Exception:
                wallet_cash = wallet_bonus = 0
        raw_wallet_total = wallet_cash + wallet_bonus

        # --- 1) Admin assigned (in-flight) ---
        if base_proc > 0:
            asset_display     = int(self.asset_cents or 0)  # negative REQUIRED
            total_display     = asset_display               # mirror Asset while processing>0
            dividends_display = min(paid_div, max(0, raw_wallet_total))
            return {
                "total_asset_cents": total_display,
                "asset_cents": asset_display,
                "dividends_cents": dividends_display,
                "processing_cents": base_proc,
            }

        # --- 2) Settled: TOTAL MUST EQUAL WALLET ---
        total_display = raw_wallet_total

        # If any approved ADMIN exists → Asset = Σ(required) (money user 'paid')
        has_any_admin = False
        try:
            UserTask = apps.get_model(self._meta.app_label, "UserTask")
            approved_admins_all = (
                UserTask.objects
                .filter(user=self.user, task_kind=UserTask.Kind.ADMIN, status=UserTask.Status.APPROVED)
            )
            has_any_admin = approved_admins_all.exists()
        except Exception:
            approved_admins_all = []

        if has_any_admin:
            paid_sum_all = 0
            try:
                for ut in approved_admins_all.only("required_cash_cents"):
                    paid_sum_all += max(0, int(getattr(ut, "required_cash_cents", 0) or 0))
            except Exception:
                pass

            # Asset shows money the user paid (capped to wallet/total)
            asset_display     = min(max(0, paid_sum_all), total_display)
            dividends_display = base_div  # untouched

            return {
                "total_asset_cents": total_display,  # == wallet (cash + bonus)
                "asset_cents": asset_display,        # cumulative paid money
                "dividends_cents": dividends_display,
                "processing_cents": 0,
            }

        # No approved admin → normal/trial behavior
        dividends_display = min(paid_div, total_display)
        asset_display     = max(0, total_display - dividends_display)

        return {
            "total_asset_cents": total_display,   # == wallet (cash + bonus)
            "asset_cents": asset_display,
            "dividends_cents": dividends_display,
            "processing_cents": 0,
        }

    # ---------- Counters & progress ----------
    def add_commission(self, commission_cents: int):
        """Increase dividends (used for regular/trial submit and admin approval)."""
        self.dividends_cents = (self.dividends_cents or 0) + int(commission_cents)
        self.save(update_fields=["dividends_cents", "updated_at"])

    def advance(self):
        """
        Increment index and, if configured:
          - block at limit
          - clear trial bonus at limit (optional)
        """
        s = tasksettngs.load()
        with transaction.atomic():
            self.current_task_index = (self.current_task_index or 0) + 1

            if s.block_on_reaching_limit and self.current_task_index >= (self.limit_snapshot or 0):
                # count cycle + block
                self.cycles_completed = (self.cycles_completed or 0) + 1
                self.is_blocked = True

                # Clear trial bonus if enabled
                if s.clear_trial_bonus_at_limit:
                    w = getattr(self.user, "wallet", None)
                    if w and (w.bonus_cents or 0) > 0:
                        cleared = int(w.bonus_cents or 0)
                        # zero the bonus and write a ledger row
                        Wallet.objects.filter(pk=w.pk).update(bonus_cents=0)
                        WalletTxn.objects.create(
                            wallet=w,
                            amount_cents=-cleared,     # negative entry (bonus removed)
                            kind="BONUS",
                            bucket="BONUS",
                            memo="Trial bonus cleared at cycle limit",
                            created_by=None,
                        )

            self.save(update_fields=[
                "current_task_index", "cycles_completed", "is_blocked", "updated_at"
            ])

    def unblock(self):
        """
        Admin unblocks → new run: reset index & refresh snapshots.
        (Dividends/Asset/Processing are retained.)
        """
        self._refresh_snapshots_from_settings()
        self.current_task_index = 0
        self.is_blocked = False
        self.last_reset_at = timezone.now()
        self.save(update_fields=[
            "current_task_index", "is_blocked",
            "limit_snapshot", "price_snapshot", "commission_snapshot",
            "last_reset_at", "updated_at"
        ])

    # ---------- Withdrawal gating ----------
    def can_withdraw(self) -> tuple[bool, str]:
        """
        Rule:
          - User may withdraw once after completing their FIRST cycle (cycles_completed >= 1).
          - After any withdrawal, the NEXT withdrawal is only allowed once TWO MORE cycles
            have been completed since that withdrawal.
        """
        try:
            s = tasksettngs.load()
            gap = int(getattr(s, "cycles_between_withdrawals", 2) or 2)
        except Exception:
            gap = 2

        if (self.cycles_completed or 0) < 1:
            return False, "Withdrawals unlock after completing your first cycle."

        if (self.last_withdraw_cycle or 0) == 0:
            return True, ""

        needed = (self.last_withdraw_cycle or 0) + gap
        if (self.cycles_completed or 0) >= needed:
            return True, ""

        remaining = max(0, needed - (self.cycles_completed or 0))
        return False, f"Withdrawals unlock after {remaining} more cycle(s)."

    def mark_withdraw_done(self):
        """
        Call this AFTER a successful withdrawal payout/transfer is executed.
        Pins the current cycles_completed as the baseline for the next window.
        """
        self.last_withdraw_cycle = int(self.cycles_completed or 0)
        self.save(update_fields=["last_withdraw_cycle", "updated_at"])

    # ===== NEW: keep Asset rules consistent AFTER withdrawal =====
    def register_withdraw(self, amount_cents: int) -> None:
        """
        Advance dividends_paid_cents by the amount actually withdrawn, so that
        future renders continue to treat commissions as 'dividends', not Asset.

        This prevents the 'commissions leaking into Asset after withdrawal' bug.
        """
        if not amount_cents or amount_cents <= 0:
            return
        with transaction.atomic():
            # Reload just in case
            prog = type(self).objects.select_for_update().only(
                "id", "dividends_cents", "dividends_paid_cents"
            ).get(pk=self.pk)

            base_div = int(prog.dividends_cents or 0)
            paid_div = int(prog.dividends_paid_cents or 0)

            # Increase paid by the withdrawn amount, but never over total dividends
            new_paid = min(base_div, paid_div + int(amount_cents))
            if new_paid != paid_div:
                prog.dividends_paid_cents = new_paid
                prog.save(update_fields=["dividends_paid_cents", "updated_at"])

    def on_withdraw_confirmed(self, amount_cents: int) -> None:
        """
        Convenience hook to use in your withdrawal-confirmation flow:
          - bumps dividends_paid_cents appropriately
          - records the cycle baseline for the next withdrawal window
        """
        self.register_withdraw(int(amount_cents or 0))
        self.mark_withdraw_done()

    # ---------- helpers ----------
    def _refresh_snapshots_from_settings(self):
        s = tasksettngs.load()
        self.limit_snapshot = s.task_limit_per_cycle
        self.price_snapshot = s.task_price
        self.commission_snapshot = s.task_commission

    def start_new_cycle(self, *, refresh_snapshots: bool = True, commit: bool = True):
        if refresh_snapshots:
            self._refresh_snapshots_from_settings()
        self.current_task_index = 0
        self.is_blocked = False
        self.last_reset_at = timezone.now()
        if commit:
            self.save(update_fields=[
                "current_task_index", "is_blocked",
                "limit_snapshot", "price_snapshot", "commission_snapshot",
                "last_reset_at", "updated_at",
            ])

    # ---------- core auto-logic ----------
    def save(self, *args, **kwargs):
        """
        Keep auto-changes even on partial updates.
        """
        old = None
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).only(
                "current_task_index", "limit_snapshot", "is_blocked", "cycles_completed"
            ).first()

        changed = set()

        # (1) Reached limit -> count cycle + block (only if not already blocked)
        if self.current_task_index >= (self.limit_snapshot or 0) and not self.is_blocked:
            if (old is None) or (old.is_blocked is False):
                self.cycles_completed = (self.cycles_completed or 0) + 1
                self.is_blocked = True
                changed.update({"cycles_completed", "is_blocked"})

        # (2) Admin unblocked after limit -> auto-roll to fresh cycle + refresh snapshots
        if old and old.is_blocked and (self.is_blocked is False):
            if old.current_task_index >= (old.limit_snapshot or 0):
                self._refresh_snapshots_from_settings()
                self.current_task_index = 0
                self.last_reset_at = timezone.now()
                changed.update({
                    "current_task_index",
                    "limit_snapshot", "price_snapshot", "commission_snapshot",
                    "last_reset_at",
                })

        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            uf = set(kwargs["update_fields"])
            uf.update(changed)
            uf.add("updated_at")
            kwargs["update_fields"] = list(uf)

        super().save(*args, **kwargs)


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


#force durectuve task
def _first_pending_directive_for(user, cycle: int, next_order: int) -> "ForcedTaskDirective | None":
    """
    Find a PENDING, not-expired directive for this user and next order.
    Preference:
      1) exact match on (cycle, order)
      2) fallback: same order, applies_on_cycle <= current cycle (old backlog), earliest first
    Auto-expire any outdated directives it touches.
    """
    from .models import ForcedTaskDirective
    now = timezone.now()

    # exact match first
    qs = (ForcedTaskDirective.objects
          .filter(user=user,
                  applies_on_cycle=cycle,
                  target_order=next_order,
                  status=ForcedTaskDirective.Status.PENDING)
          .select_related("template")
          .order_by("created_at"))

    # expire any that have passed expires_at
    expired = qs.filter(expires_at__lte=now)
    if expired.exists():
        expired.update(status=ForcedTaskDirective.Status.EXPIRED, expired_at=now, updated_at=now)

    directive = qs.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).first()
    if directive:
        return directive

    # relaxed fallback: same order, earlier-or-equal cycle, oldest first
    qs2 = (ForcedTaskDirective.objects
           .filter(user=user,
                   target_order=next_order,
                   status=ForcedTaskDirective.Status.PENDING)
           .filter(Q(applies_on_cycle=cycle) | Q(applies_on_cycle__lt=cycle))
           .select_related("template")
           .order_by("applies_on_cycle", "created_at"))

    expired2 = qs2.filter(expires_at__lte=now)
    if expired2.exists():
        expired2.update(status=ForcedTaskDirective.Status.EXPIRED, expired_at=now, updated_at=now)

    return qs2.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).first()

#ensure task progress
def ensure_task_progress(user) -> "UserTaskProgress":
    """
    Get or create a UserTaskProgress for the user.
    If created, snapshot current TaskSettings.
    If it already exists but snapshots look missing, patch them.
    """
    from django.utils import timezone
    s = tasksettngs.load()

    prog, created = UserTaskProgress.objects.get_or_create(
        user=user,
        defaults={
            "limit_snapshot": s.task_limit_per_cycle,
            "price_snapshot": s.task_price,
            "commission_snapshot": s.task_commission,
            "current_task_index": 0,
            "is_blocked": False,
            "last_reset_at": timezone.now(),
        },
    )

    # Defensive patch for legacy/empty snapshots
    if not created and (
        prog.limit_snapshot is None
        or prog.price_snapshot is None
        or prog.commission_snapshot is None
    ):
        if prog.limit_snapshot is None:
            prog.limit_snapshot = s.task_limit_per_cycle
        if prog.price_snapshot is None:
            prog.price_snapshot = s.task_price
        if prog.commission_snapshot is None:
            prog.commission_snapshot = s.task_commission
        prog.save(update_fields=["limit_snapshot", "price_snapshot", "commission_snapshot", "updated_at"])

    return prog

from django.db import transaction


#user signin reward tack
class DailyCycleSnapshot(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cycle_snapshots")
    date = models.DateField(db_index=True)  # local calendar day baseline
    cycles_completed_at_midnight = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "date")]
        indexes = [models.Index(fields=["user", "date"])]

    def __str__(self):
        return f"{self.user} @ {self.date}: {self.cycles_completed_at_midnight}"

#signreward
class SigninRewardLog(models.Model):
    """
    One row per user per date when a sign-in reward is claimed.
    Bonus rows have is_bonus=True.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signin_logs")
    date = models.DateField(default=timezone.localdate, db_index=True)
    amount_cents = models.IntegerField(default=0)
    is_bonus = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "date", "is_bonus")]
        indexes = [models.Index(fields=["user", "date"]), models.Index(fields=["user", "is_bonus"])]

    def __str__(self):
        tag = "BONUS" if self.is_bonus else "DAY"
        return f"{self.user} {tag} {self.date} {self.amount_cents}c"



# =========================
# Fortune Card: models/rules
# =========================

class FortuneCardRule(models.Model):
    class Kind(models.TextChoices):
        CASH   = "CASH", "Cash reward"
        GOLDEN = "GOLDEN", "Golden (admin)"

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.CASH)

    # Scope rule to one user if set; NULL => global
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="fortune_rules",
        help_text="If set, only this user sees the card at that slot.",
    )

    # Slot (cycle + order)
    cycle_number = models.PositiveIntegerField(help_text="Cycle to match (0 = first cycle).")
    order_index  = models.PositiveIntegerField(help_text="1-based order inside the cycle.")

    # Cash reward
    reward_amount_cents = models.BigIntegerField(default=0)

    # Golden (admin) → which ADMIN template to force
    golden_template = models.ForeignKey(
        'UserTaskTemplate',
        null=True, blank=True,
        on_delete=models.PROTECT,
        limit_choices_to={'is_admin_task': True},
        help_text="Admin task to spawn for GOLDEN rewards.",
    )

    active     = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_fortune_rules",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["active", "cycle_number", "order_index"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["target_user", "cycle_number", "order_index"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        slot = f"cycle {self.cycle_number} @ {self.order_index}"
        who  = f" for u={self.target_user_id}" if self.target_user_id else ""
        if self.kind == self.Kind.CASH:
            amt = f"{self.reward_amount_cents/100:.2f}"
            return f"[CASH €{amt}] {slot}{who}"
        return f"[GOLDEN {self.golden_template_id or '-'}] {slot}{who}"


class FortuneCardGrant(models.Model):
    """One concrete offer to a user at a specific cycle+order."""
    class Status(models.TextChoices):
        OFFERED   = "OFFERED", "Offered"
        CLICKED   = "CLICKED", "Clicked"
        CREDITED  = "CREDITED", "Credited (cash)"
        CONVERTED = "CONVERTED", "Converted to task (golden)"
        CANCELED  = "CANCELED", "Canceled"
        EXPIRED   = "EXPIRED", "Expired"

    user   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="fortune_grants")
    rule   = models.ForeignKey(FortuneCardRule, on_delete=models.PROTECT, related_name="grants")

    cycle_number = models.PositiveIntegerField()
    order_index  = models.PositiveIntegerField()

    # Snapshots
    kind               = models.CharField(max_length=10, default="CASH")
    amount_cents       = models.BigIntegerField(default=0)
    golden_template_id = models.IntegerField(default=0)

    picked_box = models.PositiveIntegerField(default=0)  # 1..3
    status     = models.CharField(max_length=10, choices=Status.choices, default=Status.OFFERED)

    user_task  = models.ForeignKey(
        'UserTask', null=True, blank=True, on_delete=models.SET_NULL, related_name="fortune_origin"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "cycle_number", "order_index"]),
            models.Index(fields=["status"]),
        ]
        unique_together = [("user", "cycle_number", "order_index")]

    def __str__(self):
        return f"Grant u={self.user_id} cyc={self.cycle_number}@{self.order_index} [{self.status}]"


# =========================
# Fortune Card: helpers/api
# =========================

def _active_rule_for_slot(user, cycle: int, order_index: int):
    """
    Prefer a user-targeted rule; else fall back to global.
    """
    now = timezone.now()
    base = (FortuneCardRule.objects
            .filter(active=True, cycle_number=cycle, order_index=order_index)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)))

    rule = base.filter(target_user=user).order_by("-created_at").first()
    if rule:
        return rule
    return base.filter(target_user__isnull=True).order_by("-created_at").first()


def maybe_offer_fortune(user) -> "FortuneCardGrant | None":
    """
    Only return the grant while it's still OFFERED.
    If user has CLICKED/CREDITED/CONVERTED, do NOT return it (stops popup).
    """
    from .models import ensure_task_progress  # avoid cycles
    prog = ensure_task_progress(user)
    cycle = prog.cycles_completed
    order_index = prog.natural_next_order

    rule = _active_rule_for_slot(user, cycle, order_index)
    if not rule:
        return None

    grant, _ = FortuneCardGrant.objects.get_or_create(
        user=user, cycle_number=cycle, order_index=order_index,
        defaults=dict(
            rule=rule,
            kind=rule.kind,
            amount_cents=rule.reward_amount_cents,
            golden_template_id=rule.golden_template_id or 0,
        ),
    )

    # Only surface when OFFERED
    if grant.status != FortuneCardGrant.Status.OFFERED:
        return None
    return grant


def grant_cash_reward(grant: FortuneCardGrant):
    """
    Credit wallet and set grant -> CREDITED. Idempotent.
    """
    if grant.kind != FortuneCardRule.Kind.CASH:
        raise Http404("Not a cash grant")

    if grant.status == FortuneCardGrant.Status.CREDITED:
        return grant

    wallet = getattr(grant.user, "wallet", None)
    if not wallet:
        raise ValueError("User has no wallet")

    from django.db import transaction
    with transaction.atomic():
        wallet.balance_cents = int(wallet.balance_cents or 0) + int(grant.amount_cents or 0)
        wallet.save(update_fields=["balance_cents"])
        grant.status = FortuneCardGrant.Status.CREDITED
        grant.save(update_fields=["status", "updated_at"])
    return grant


@transaction.atomic
def convert_to_golden_task(grant: FortuneCardGrant) -> "UserTask":
    """
    Create ADMIN task via a ForcedTaskDirective for THIS slot and
    set required deposit to CASH shortfall (price - wallet.cash).
    Mark grant -> CONVERTED so popup stops.
    """
    from .models import (
        ensure_task_progress, UserTaskTemplate, ForcedTaskDirective,
        spawn_next_task_for_user, UserTask, UserTaskProgress,
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

    # Will create ADMIN UserTask and call mark_admin_assigned_effects()
    task = spawn_next_task_for_user(grant.user)

    # ---- OVERRIDE required cash to CASH shortfall (price - wallet.cash) ----
    task = UserTask.objects.select_for_update().only(
        "id", "price_used", "commission_used",
        "assignment_total_display_cents", "required_cash_cents",
    ).get(pk=task.pk)

    price_cents = int((task.price_used or Decimal("0")) * 100)
    commission_cents = int((task.commission_used or Decimal("0")) * 100)

    wallet = getattr(grant.user, "wallet", None)
    cash_now = int(getattr(wallet, "balance_cents", 0) or 0)  # CASH ONLY
    required = max(0, price_cents - cash_now)

    if (task.assignment_total_display_cents != cash_now) or (task.required_cash_cents != required):
        task.assignment_total_display_cents = cash_now
        task.required_cash_cents = required
        task.save(update_fields=["assignment_total_display_cents", "required_cash_cents", "updated_at"])

    # Re-apply dashboard “assigned” using CASH shortfall — not wallet total
    prog = UserTaskProgress.objects.select_for_update().get(pk=prog.pk)
    prog.set_state_admin_assigned(
        price_cents=price_cents,
        admin_commission_cents=commission_cents,
        required_cents=required,
    )

    # finalize grant
    grant.user_task = task
    grant.status = FortuneCardGrant.Status.CONVERTED
    grant.save(update_fields=["user_task", "status", "updated_at"])

    return task



