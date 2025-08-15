# models.py
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.crypto import get_random_string
from django.utils import timezone



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

# User wallet
class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance_cents = models.BigIntegerField(default=0)
    pending_cents = models.BigIntegerField(default=0)

    def balance(self): return self.balance_cents / 100

# Saved payout addresses (for withdrawals)
class PayoutAddress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payout_addresses")
    label = models.CharField(max_length=64, blank=True)
    address_type = models.CharField(max_length=10, choices=AddressType.choices)
    address = models.CharField(max_length=128)
    is_verified = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "address_type", "address")
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
