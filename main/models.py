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
from django.utils.translation import gettext_lazy as _

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

    # IP + Country tracking
    signup_ip = models.GenericIPAddressField(blank=True, null=True)
    signup_country = models.CharField(max_length=100, blank=True, null=True)
    last_login_ip = models.GenericIPAddressField(blank=True, null=True)
    last_login_country = models.CharField(max_length=100, blank=True, null=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self):
        return self.phone

    def save(self, *args, **kwargs):
        if self.phone:
            self.phone = self.phone.replace(" ", "").replace("-", "")
        super().save(*args, **kwargs)


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
