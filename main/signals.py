# main/signals.py
import ipaddress
import logging
import requests
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
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



User = get_user_model()

@receiver(post_save, sender=User)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(user=instance)
