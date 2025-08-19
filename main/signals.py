# main/signals.py
import ipaddress
import logging
import requests
from decimal import Decimal, ROUND_HALF_UP
from django.apps import apps
from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.contrib.auth.signals import user_logged_in

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.apps import apps  # load AUTH_USER_MODEL safely at runtime

from .models import Wallet

log = logging.getLogger(__name__)

IPAPI_URL = "https://ipapi.co/{ip}/country_name/"
IPAPI_TIMEOUT = 2.5  # tight so login never feels sluggish


# --- IP helpers ---
def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return not (addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local)
    except Exception:
        return False

def _extract_client_ip(request):
    """
    Prefer the first public IP in X-Forwarded-For; fallback to REMOTE_ADDR.
    """
    if not request:
        return None

    xff = request.META.get("HTTP_X_FORWARDED_FOR") or ""
    if xff:
        # XFF can look like: "client, proxy1, proxy2"
        for raw in xff.split(","):
            ip = raw.strip()
            if _is_public_ip(ip):
                return ip

    ip = request.META.get("REMOTE_ADDR")
    # If REMOTE_ADDR is public, use it; else return it anyway (useful for dev/local logs)
    return ip if (ip and _is_public_ip(ip)) else (ip or None)


# --- Country lookup ---
def _country_from_ip(ip: str):
    """
    Free lookup via ipapi.co. Returns country name or None.
    Swallows network hiccups; logs non-200 responses.
    """
    if not ip or not _is_public_ip(ip):
        return None
    try:
        r = requests.get(IPAPI_URL.format(ip=ip), timeout=IPAPI_TIMEOUT)
        if r.status_code == 200:
            name = (r.text or "").strip()
            return name or None
        elif r.status_code in (403, 429):
            log.info("ipapi.co refused or rate-limited for IP %s (status %s)", ip, r.status_code)
        else:
            log.warning("ipapi.co error for IP %s: status %s", ip, r.status_code)
    except requests.RequestException as e:
        log.warning("ipapi.co request failed for IP %s: %s", ip, e)
    return None


# --- Signal: capture after successful login ---
@receiver(user_logged_in)
def capture_login_ip(sender, request, user, **kwargs):
    """
    Store IP and (best-effort) country on every successful login.
    - Always saves the IP if we have one.
    - Saves country when the external service responds in time.
    """
    ip = _extract_client_ip(request)

    updated_fields = []
    if ip and getattr(user, "last_login_ip", None) != ip:
        user.last_login_ip = ip
        updated_fields.append("last_login_ip")

    country = _country_from_ip(ip) if ip else None
    if country and getattr(user, "last_login_country", None) != country:
        user.last_login_country = country
        updated_fields.append("last_login_country")

    if updated_fields:
        user.save(update_fields=updated_fields)


# --- Signal: create wallet AND award trial bonus on user creation (once) ---
@receiver(post_save, dispatch_uid="wallet_create_and_bonus")
def wallet_create_and_trial_bonus(sender, instance, created, **kwargs):
    """
    Runs for the AUTH_USER_MODEL only. On create:
      - Ensure a Wallet exists
      - If enabled and not yet granted, credit the signup bonus into BONUS BALANCE
        (wallet.bonus_cents) and stamp trial_bonus_at.
      - Also writes a WalletTxn row if the model exists (kind=BONUS, bucket=BONUS).
    """
    UserModel = apps.get_model(settings.AUTH_USER_MODEL)  # apps are loaded now
    if sender is not UserModel or not created:
        return

    # Feature toggles
    bonus_enabled = getattr(settings, "TRIAL_BONUS_ENABLED", True)
    bonus_eur = int(getattr(settings, "TRIAL_BONUS_EUR", 300))
    if not bonus_enabled or bonus_eur <= 0:
        # Still ensure a wallet exists for the user
        Wallet.objects.get_or_create(user=instance)
        return

    bonus_cents = bonus_eur * 100

    with transaction.atomic():
        wallet, _ = Wallet.objects.select_for_update().get_or_create(user=instance)

        # Guard: field may not exist yet if migration not applied; getattr handles it.
        if getattr(wallet, "trial_bonus_at", None) is not None:
            return  # already granted

        # Atomic increment on BONUS bucket + stamp time (prevents double-grant)
        updated = Wallet.objects.filter(pk=wallet.pk, trial_bonus_at__isnull=True).update(
            bonus_cents=F("bonus_cents") + bonus_cents,
            trial_bonus_at=timezone.now(),
        )
        if not updated:
            return  # race/duplicate guard

        # Write a ledger row if model is available
        try:
            from .models import WalletTxn  # avoid import cycle issues at module import time
            WalletTxn.objects.create(
                wallet=wallet,
                amount_cents=bonus_cents,
                kind="BONUS",
                bucket="BONUS",   # <-- goes to the bonus bucket
                memo="Signup trial bonus",
                created_by=None,
            )
        except Exception:
            # If the ledger model isn't present yet, just skip logging.
            pass





# --- Auto-credit wallet when a deposit is marked successful ---

SUCCESS_STATUSES = {"confirmed", "completed", "credited", "success"}

def _to_cents(amount) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

@receiver(post_save, dispatch_uid="auto_credit_wallet_on_deposit")
def auto_credit_wallet_on_deposit(sender, instance, created, **kwargs):
    """
    Credits CASH balance when a DepositRequest reaches a success status.
    Idempotent: will not double-credit if a matching WalletTxn already exists.
    """
    # Get your DepositRequest model safely (avoid hard import)
    DepositRequest = apps.get_model("main", "DepositRequest")  # <-- change "main" if your app label differs
    if sender is not DepositRequest:
        return

    # Only when in a success status
    if getattr(instance, "status", None) not in SUCCESS_STATUSES:
        return

    # Must have user + amount
    user = getattr(instance, "user", None)
    amount = getattr(instance, "amount", None)
    if not user or amount is None:
        return

    Wallet = apps.get_model("main", "Wallet")       # <-- adjust app label if needed
    WalletTxn = None
    try:
        WalletTxn = apps.get_model("main", "WalletTxn")
    except Exception:
        pass  # ledger optional

    # Build an idempotency key via memo (uses reference if present)
    reference = getattr(instance, "reference", None) or f"dep#{instance.pk}"
    memo = f"Deposit {reference}"

    cents = _to_cents(amount)
    if cents <= 0:
        return

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)

        # Idempotency: if a matching ledger row exists, assume already credited
        if WalletTxn:
            if WalletTxn.objects.filter(wallet=wallet, kind="DEPOSIT", memo=memo).exists():
                return

        # Credit CASH (real money)
        Wallet.objects.filter(pk=wallet.pk).update(balance_cents=F("balance_cents") + cents)

        # Write ledger row if model exists
        if WalletTxn:
            try:
                # If you use the split buckets model:
                WalletTxn.objects.create(
                    wallet=wallet,
                    amount_cents=cents,
                    kind="DEPOSIT",
                    bucket="CASH",       # very important: real money
                    memo=memo,
                    created_by=None,
                )
            except TypeError:
                # If your WalletTxn has no `bucket` field yet, fall back:
                WalletTxn.objects.create(
                    wallet=wallet,
                    amount_cents=cents,
                    kind="DEPOSIT",
                    memo=memo,
                    created_by=None,
                )

        # Optionally stamp credited_at if your model has it
        if hasattr(instance, "credited_at") and getattr(instance, "credited_at") is None:
            type(instance).objects.filter(pk=instance.pk, credited_at__isnull=True).update(
                credited_at=timezone.now()
            )
