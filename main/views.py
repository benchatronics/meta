# main/views.py
from __future__ import annotations
#cache
from django.views.decorators.cache import cache_page
from django_ratelimit.decorators import ratelimit


from django.conf import settings

from django.utils.translation import get_language

from django.http import HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden, HttpResponseBadRequest

# -------- Standard library --------
import base64
import hashlib
import hmac
import json
import random
import secrets
import time
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
import uuid
#next url

from urllib.parse import urlencode
from django.utils.http import url_has_allowed_host_and_scheme

# -------- Third-party --------
import phonenumbers

# -------- Django --------
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import LogEntry, CHANGE
#from django.contrib.admin.views.decorators import staff_member_member_required as staff_member_required  # if you had this variant
from django.contrib.admin.views.decorators import staff_member_required  # keep this one if used
from django.contrib.auth import (
    authenticate, get_user_model, login, logout, update_session_auth_hash
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Value, BooleanField
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

# Provide both names if some code uses `static_url`
static_url = static

# -------- Local (project) --------
from .country_codes import COUNTRY_CODES
from .forms import (
    SignupForm, LoginForm, StaffResetPasswordForm,
    WithdrawalForm, AddressForm, DepositForm, CURRENCY_SYMBOL,
    ChangePasswordForm, ProfileUpdateForm,
    MAX_AVATAR_MB, ALLOWED_IMG_TYPES,
    SetTxPinForm, ChangeTxPinForm,
)
from .models import (
    Wallet, PayoutAddress, WithdrawalRequest,
    AddressType, Currency,
    DepositAddress, DepositRequest, Network,
    Hotel, Favorite, InfoPage, Announcement,
)
from .services import confirm_deposit

# Task helpers (standardize on .task, not .tasks)
#from .task import (
   # account_snapshot,

    #count_approved,
    #detect_phase,
    #pick_template_for_phase,
    #latest_vip_task,
    #complete_trial_task,
   # complete_normal_task,
    #vip_deposit_shortfall_cents,
#)


def _get_next_url(request, default_name="task_take"):
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return reverse(default_name)

def _qs_next(request):
    nxt = request.GET.get("next") or request.POST.get("next")
    return f"?{urlencode({'next': nxt})}" if nxt else ""



def rewards(request):

    ctx = {
        "active_page": "rewards",
    }
    return render(request, "meta_search/rewards.html", ctx)


#witdrawal pin
@login_required
def set_tx_pin(request):
    if request.method == "POST":
        form = SetTxPinForm(request.POST, user=request.user)
        if form.is_valid():
            request.user.set_tx_pin(form.cleaned_data["tx_pin1"])
            messages.success(request, _("Withdrawal password set."))
            return redirect("withdrawal")  # or wherever you fit go
    else:
        form = SetTxPinForm(user=request.user)
    return render(request, "meta_search/set_tx_pin.html", {"form": form})

#change witdrawal pin
@login_required
def change_tx_pin(request):
    if not request.user.has_tx_pin():
        messages.info(request, _("You don’t have a withdrawal password yet. Set one first."))
        return redirect("set_tx_pin")

    if request.method == "POST":
        form = ChangeTxPinForm(request.POST, user=request.user)
        if form.is_valid():
            request.user.set_tx_pin(form.cleaned_data["new_tx_pin1"])
            messages.success(request, _("Withdrawal password updated."))
            return redirect("withdrawal")
    else:
        form = ChangeTxPinForm(user=request.user)
    return render(request, "meta_search/change_tx_pin.html", {"form": form})



#profile settings
@login_required
def profile_settings(request):
    user = request.user

    if request.method == "POST":
        # Handle explicit delete of the uploaded file (does not touch avatar_url)
        if request.POST.get("delete_avatar") == "1":
            if getattr(user, "avatar", None):
                try:
                    user.avatar.delete(save=False)  # remove file from storage
                except Exception:
                    pass
            user.avatar = None
            user.save(update_fields=["avatar"])
            messages.success(request, "Profile image removed.")
            return redirect("profile_settings")

        # Normal update (nickname, avatar_url, optional new avatar file)
        old_avatar_name = user.avatar.name if getattr(user, "avatar", None) else None
        form = ProfileUpdateForm(request.POST, request.FILES, instance=user)

        if form.is_valid():
            # We want to prefer the file if provided, and optionally clear URL.
            new_file = request.FILES.get("avatar")

            updated_user = form.save(commit=False)
            if new_file:
                # Prefer file over URL; clear URL so templates consistently pick the file
                updated_user.avatar_url = ""

            updated_user.save()

            # If a new file replaced an old one, delete the old file from storage
            if new_file and old_avatar_name and old_avatar_name != updated_user.avatar.name:
                try:
                    user._meta.get_field("avatar").storage.delete(old_avatar_name)
                except Exception:
                    pass

            messages.success(request, "Profile updated.")
            return redirect("profile_settings")
    else:
        form = ProfileUpdateForm(instance=user)

    # Build preview source: uploaded file -> URL field -> static placeholder
    avatar_src = None
    try:
        if getattr(user, "avatar", None):
            avatar_src = user.avatar.url
    except Exception:
        avatar_src = None
    if not avatar_src:
        avatar_src = getattr(user, "avatar_url", None)
    if not avatar_src:
        avatar_src = static("meta_search/images/avatar-placeholder.png")

    context = {
      "form": form,
      "avatar_src": avatar_src,
      "user_id_value": user.id,
      "phone_value": getattr(user, "phone", "-"),
      "language_value": (getattr(request, "LANGUAGE_CODE", "en") or "en").upper(),
      "max_avatar_mb": MAX_AVATAR_MB,
      "allowed_types": list(ALLOWED_IMG_TYPES),
      }
    return render(request, "meta_search/profile_settings.html", context)


# -----------------------------
# Password reset (by phone + OTP)
# -----------------------------
def _normalize_to_e164(country_dial: str, raw: str) -> str:
    cleaned = []
    for ch in (raw or "").strip():
        if ch.isdigit():
            cleaned.append(ch)
        elif ch == '+' and not cleaned:
            cleaned.append(ch)
    number = ''.join(cleaned)
    if not number.startswith('+'):
        number = f"{country_dial}{number.lstrip('0')}"
    if number.startswith(country_dial + '0'):
        number = country_dial + number[len(country_dial):].lstrip('0')
    try:
        parsed = phonenumbers.parse(number, None)
    except phonenumbers.NumberParseException:
        raise forms.ValidationError(_("Invalid phone number format."))
    if not phonenumbers.is_possible_number(parsed):
        raise forms.ValidationError(_("This phone number is not possible for the selected country."))
    if not phonenumbers.is_valid_number(parsed):
        raise forms.ValidationError(_("This phone number is not valid for the selected country."))
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


class PasswordResetPhoneForm(forms.Form):
    country_code = forms.ChoiceField(
        choices=[(dial, disp) for dial, disp, _, _ in COUNTRY_CODES],
        widget=forms.Select(attrs={'class': 'country-select'})
    )
    phone_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'autocomplete': 'tel', 'class': 'cc-input'})
    )

    def clean(self):
        cleaned = super().clean()
        dial = cleaned.get('country_code')
        raw = cleaned.get('phone_number')
        if dial and raw:
            cleaned['phone'] = _normalize_to_e164(dial, raw)
        return cleaned


class PasswordResetOTPForm(forms.Form):
    otp = forms.CharField(max_length=6, label=_("Verification code"))
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={'id': 'new_password1', 'class': 'pwd-input'}), label=_("New password"))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={'id': 'new_password2', 'class': 'pwd-input'}), label=_("Confirm new password"))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('new_password1') != cleaned.get('new_password2'):
            self.add_error('new_password2', _("Passwords do not match."))
        return cleaned


from twilio.base.exceptions import TwilioRestException
from .twilio_sms import send_sms as _twilio_send_sms

def _send_sms(phone_e164: str, message: str) -> None:
    _twilio_send_sms(phone_e164, message)



def _generate_otp() -> str:
    return f"{random.randint(100000, 999999)}"


User = get_user_model()

from twilio.base.exceptions import TwilioRestException
@csrf_protect
def password_reset_start(request):
    """
    Step 1: User inputs phone. We send an OTP via SMS and store it for 10 minutes.
    """
    if request.method == 'POST':
        form = PasswordResetPhoneForm(request.POST)
        if form.is_valid():
            phone = form.cleaned_data['phone']
            try:
                user = User.objects.get(**{User.USERNAME_FIELD: phone})
            except User.DoesNotExist:
                messages.error(request, _("No account found for that phone."))
                return render(request, 'meta_search/password_reset_phone.html', {'form': form})

            # --- generate + cache OTP (10 minutes) ---
            otp = _generate_otp()
            cache_key = f"pr_otp:{phone}"
            cache.set(cache_key, otp, timeout=600)

            # --- attempt to send via Twilio ---
            try:
                _send_sms(
                    phone,
                    _("Your reset code is: %(otp)s. It expires in 10 minutes.") % {"otp": otp}
                )
            except TwilioRestException:
                cache.delete(cache_key)  # rollback so user can request again cleanly
                messages.error(request, _("We couldn’t send the code. Please try again in a moment."))
                return render(request, 'meta_search/password_reset_phone.html', {'form': form})
            except Exception:
                cache.delete(cache_key)
                messages.error(request, _("Unexpected error sending code. Try again later."))
                return render(request, 'meta_search/password_reset_phone.html', {'form': form})

            # --- success path ---
            request.session['pr_phone'] = phone
            messages.success(request, _("We sent a verification code to your phone."))
            return redirect('password_reset_verify')
        else:
            messages.error(request, _("Please correct the errors below."))
    else:
        form = PasswordResetPhoneForm()

    return render(request, 'meta_search/password_reset_phone.html', {'form': form})



@csrf_protect
def password_reset_verify(request):
    """
    Step 2: User enters OTP + new password. If OK, we set the password.
    """
    phone = request.session.get('pr_phone')
    if not phone:
        messages.info(request, _("Start by entering your phone number."))
        return redirect('password_reset_start')

    if request.method == 'POST':
        form = PasswordResetOTPForm(request.POST)
        if form.is_valid():
            sent_otp = cache.get(f"pr_otp:{phone}")
            if not sent_otp or form.cleaned_data['otp'] != sent_otp:
                messages.error(request, _("Invalid or expired code."))
                return render(request, 'meta_search/password_reset_verify.html', {'form': form})

            try:
                user = User.objects.get(**{User.USERNAME_FIELD: phone})
            except User.DoesNotExist:
                messages.error(request, _("Account not found. Please start over."))
                return redirect('password_reset_start')

            spf = SetPasswordForm(user, {
                'new_password1': form.cleaned_data['new_password1'],
                'new_password2': form.cleaned_data['new_password2'],
            })
            if spf.is_valid():
                spf.save()
                cache.delete(f"pr_otp:{phone}")
                request.session.pop('pr_phone', None)
                messages.success(request, _("Your password has been reset. You can now sign in."))
                return redirect('signin')

            for errs in spf.errors.values():
                for e in errs:
                    messages.error(request, e)
        else:
            messages.error(request, _("Please correct the errors below."))
    else:
        form = PasswordResetOTPForm()

    return render(request, 'meta_search/password_reset_verify.html', {'form': form})


# -----------------------------
# Auth & Index
# -----------------------------
@cache_page(60*5)  # 5 minutes
def index(request):
    """Homepage."""
    return render(request, 'meta_search/index.html')


@csrf_protect
@never_cache
def signin(request):
    # If user is already signed in, don't show the signin page
    if request.user.is_authenticated:
        messages.info(request, _("You're already signed in."))
        return redirect('user_dashboard')
    """
    Phone-based login using LoginForm (country_code + phone_number + password).
    - Validates & normalizes phone to E.164 in the form.
    - Supports ?next=/path for post-login redirect.
    """
    redirect_to = request.GET.get('next') or request.POST.get('next') or 'user_dashboard'

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            phone = form.cleaned_data.get('phone')  # normalized E.164
            password = form.cleaned_data.get('password')
            user = authenticate(request, username=phone, password=password)
            if user is not None:
                login(request, user)

                from .signals import _extract_client_ip, _country_from_ip

                ip = _extract_client_ip(request)
                updated = []
                if ip and getattr(request.user, "last_login_ip", None) != ip:
                    request.user.last_login_ip = ip
                    updated.append("last_login_ip")

                country = _country_from_ip(ip) if ip else None
                if country and getattr(request.user, "last_login_country", None) != country:
                    request.user.last_login_country = country
                    updated.append("last_login_country")

                if updated:
                    request.user.save(update_fields=updated)

                messages.success(request, _("Welcome back!"))
                return redirect(redirect_to)
            else:
                messages.error(request, _("Invalid phone or password."))
        else:
            messages.error(request, _("Please correct the errors below."))
    else:
        form = LoginForm()

    return render(request, 'meta_search/signin.html', {
        'form': form,
        'next': redirect_to,
    })

@never_cache
@ratelimit(key='ip', rate='5/m', block=True)
def signup_view(request):
    if request.user.is_authenticated:
        messages.info(request, _("You're already signed in."))
        return redirect('user_dashboard')
    """
    Signup view passing request into the form (for IP & country capture).
    Auto-logs in after successful signup.
    """
    redirect_to = request.GET.get('next') or request.POST.get('next') or 'user_dashboard'

    if request.method == 'POST':
        form = SignupForm(request.POST, request=request)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, _("Account created!"))
            return redirect(redirect_to)
        else:
            messages.error(request, _("Please correct the errors below."))
    else:
        form = SignupForm(request=request)

    return render(request, 'meta_search/signup.html', {
        'form': form,
        'next': redirect_to,
    })


def signout(request):
    """Logout the current user and send them to the sign-in page."""
    logout(request)
    messages.info(request, _("You have been logged out."))
    return redirect('signin')


# -----------------------------
# Staff password reset (by phone)
# -----------------------------
def _normalize_to_e164_any(raw: str, default_country_dial: str = "+234") -> str:
    """
    Normalize any phone into E.164. If no '+', assume default country and strip leading 0.
    """
    if not raw:
        return None
    raw = raw.strip()
    digits = []
    for ch in raw:
        if ch.isdigit():
            digits.append(ch)
        elif ch == '+' and not digits:
            digits.append(ch)
    number = ''.join(digits) or ""
    if not number:
        return None
    if not number.startswith('+'):
        number = f"{default_country_dial}{number.lstrip('0')}"
    try:
        parsed = phonenumbers.parse(number, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


@staff_member_required
@transaction.atomic
def support_reset_password(request):
    """
    Staff-only page to reset a user's password by phone.
    """
    form = StaffResetPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        raw_phone = form.cleaned_data["phone"]
        new_password = form.cleaned_data["new_password1"]

        phone_e164 = _normalize_to_e164_any(raw_phone, default_country_dial="+234")
        if not phone_e164:
            messages.error(request, _("Invalid phone format. Please enter a valid number."))
            return render(request, "meta_search/support_reset_password.html", {"form": form})

        try:
            user = User.objects.get(phone=phone_e164)
        except User.DoesNotExist:
            messages.error(request, _("No user found with phone %(phone)s.") % {"phone": phone_e164})
            return render(request, "meta_search/support_reset_password.html", {"form": form})

        user.set_password(new_password)
        user.save(update_fields=["password"])

        # Audit trail in Admin log
        LogEntry.objects.log_action(
            user_id=request.user.pk,
            content_type_id=ContentType.objects.get_for_model(User).pk,
            object_id=user.pk,
            object_repr=str(user),
            action_flag=CHANGE,
            change_message=f"Password reset by staff at {timezone.now().isoformat()}",
        )

        messages.success(request, _("Password reset for %(phone)s.") % {"phone": phone_e164})
        return redirect("support_reset_password")

    return render(request, "meta_search/support_reset_password.html", {"form": form})

# user_dashboard

@login_required
@never_cache
def user_dashboard(request):
    """
    Render dashboard with three tabs.
    GET params:
      - location: Hotel.city (iexact)
      - date:     Hotel.available_date (YYYY-MM-DD)
      - rating:   Hotel.score >= value
      - tab:      'recommended' | 'popular' | 'rating'
    """
    user = request.user

    # Incoming filters
    rating_param   = (request.GET.get("rating") or "").strip()
    location_param = (request.GET.get("location") or "").strip()
    date_param     = (request.GET.get("date") or "").strip()

    # Active tab (prefer Rating if rating filter present)
    active_tab = request.GET.get("tab") or ("rating" if rating_param else "recommended")

    # Favorited subquery
    fav_subq = Favorite.objects.filter(user=user, hotel=OuterRef("pk"))

    # Base queryset
    base = (
        Hotel.objects.filter(is_published=True)
        .select_related("country")
        .annotate(
            is_favorited=Exists(fav_subq),
            favorites_total=Count("favorites", distinct=True),
        )
    )

    # Global filters
    if location_param:
        base = base.filter(city__iexact=location_param)
    if date_param:
        d = parse_date(date_param)
        if d:
            base = base.filter(available_date=d)

    # Rating tab
    rating_qs = base
    if rating_param:
        try:
            rating_value = float(rating_param)
            rating_qs = rating_qs.filter(score__gte=rating_value)
        except ValueError:
            pass

    # Build tabs
    recommended_hotels = base.filter(is_recommended=True).order_by("-created_at", "-score", "name")[:12]
    popular_hotels     = base.order_by("-popularity", "-created_at", "name")[:12]
    rating_hotels      = rating_qs.order_by("-score", "-created_at", "name")[:12]

    # --- Dashboard header helpers ---
    nickname = (getattr(user, "nickname", "") or "").strip()
    profile_cta = "View Profile" if nickname else "Complete your profile"

    # Robust avatar resolution (ONLY your CustomUser fields)
    if getattr(user, "avatar", None):                 # ImageField with a file
        try:
            avatar_url = user.avatar.url
        except Exception:
            avatar_url = None
    else:
        avatar_url = None

    if not avatar_url and getattr(user, "avatar_url", ""):
        avatar_url = user.avatar_url  # URLField

    if not avatar_url:
        avatar_url = static("meta_search/images/avatar-placeholder.png")

    context = {
        "hotel_tabs": [
            ("recommended", recommended_hotels),
            ("popular", popular_hotels),
            ("rating", rating_hotels),
        ],
        "active_tab": active_tab,
        "profile_cta": profile_cta,
        "user_avatar": avatar_url,  # handy for templates that expect it
    }
    return render(request, "meta_search/user_dashboard.html", {**context, "active_page":  "home"})


# -----------------------------
# Favorites (AJAX)
# -----------------------------
@login_required
@require_POST
def toggle_favorite(request, slug):
    """
    Toggle favorite for the current user. Returns JSON for AJAX.
    URL: /favorite/<slug>/
    """
    hotel = get_object_or_404(Hotel, slug=slug, is_published=True)
    fav_qs = Favorite.objects.filter(user=request.user, hotel=hotel)

    if fav_qs.exists():
        fav_qs.delete()
        favorited = False
    else:
        Favorite.objects.create(user=request.user, hotel=hotel)
        favorited = True

    return JsonResponse({
        "ok": True,
        "slug": slug,
        "favorited": favorited,
        "count": hotel.favorites.count(),
    })


# -----------------------------
# Wallet / Withdrawal
# -----------------------------
from .models import WithdrawalRequest, WithdrawalStatus, WalletTxn

WITHDRAW_KINDS = ["WITHDRAW", "PAYOUT", "CASH_OUT"]  # cover all your withdrawal kinds

from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import WalletTxn, WithdrawalRequest, WithdrawalStatus  # adjust if needed

@login_required
@never_cache
def wallet_view(request):
    w = request.user.wallet

    # --- Base txns (DB) - defensive newest-first ordering ---
    txns_all = w.txns.order_by('-created_at', '-id')

    ctx = {
        "txns": txns_all,                                   # used by tabs
        "txns_all": txns_all,
        "txns_deposit": txns_all.filter(kind__iexact="DEPOSIT"),
        # include both ADJUST & COMMISSION (template treats both as commission)
        "txns_commission": txns_all.filter(kind__in=["ADJUST", "COMMISSION"]),
    }

    # --------- WITHDRAWALS: combine ledger + requests, no duplicates ---------
    # 1) Ledger withdrawals (already negative cents)
    ledger_qs = (
        txns_all
        .filter(kind__in=WITHDRAW_KINDS)
        .values("created_at", "amount_cents", "bucket", "id", "external_ref")
    )

    ledger_rows = []
    ledger_refs = set()  # for external_ref-based de-dup

    for r in ledger_qs:
        ledger_rows.append({
            "id": r["id"],                                  # <-- for modal
            "source": "ledger",
            "created_at": r["created_at"],
            "amount_cents": int(r["amount_cents"]),         # negative debits
            "status": "confirmed",
            "bucket": (r["bucket"] or "CASH"),
            "seq": ("L", r["id"]),                          # tiebreaker for stable sort
        })
        if r["external_ref"]:
            ledger_refs.add(r["external_ref"])

    # 2) Requests: include pending/failed always; confirmed only if no matching ledger
    req_rows = []
    for r in WithdrawalRequest.objects.filter(user=request.user).values(
        "id", "created_at", "amount_cents", "status"
    ):
        status = r["status"]
        amount_cents_pos = int(r["amount_cents"])
        amount_cents_as_debit = -amount_cents_pos
        created_at = r["created_at"]

        include = True
        if status == WithdrawalStatus.CONFIRMED:
            # A) external_ref match (preferred if you set external_ref=f"wd:{wr.id}" on the ledger)
            if f"wd:{r['id']}" in ledger_refs:
                include = False
            else:
                # B) amount+time window match (±10 min) to avoid dup if external_ref wasn't set
                lo = created_at - timedelta(minutes=10)
                hi = created_at + timedelta(minutes=10)
                exists_close = WalletTxn.objects.filter(
                    wallet=w,
                    amount_cents=amount_cents_as_debit,
                    created_at__range=(lo, hi),
                    kind__in=WITHDRAW_KINDS,
                ).exists()
                if exists_close:
                    include = False

        if include:
            req_rows.append({
                "id": r["id"],                               # <-- for modal
                "source": "request",
                "created_at": created_at,
                "amount_cents": amount_cents_as_debit,      # show as debit
                "status": status,                            # pending/confirmed/failed
                "bucket": "CASH",
                "seq": ("R", r["id"]),                       # tiebreaker
            })

    # Combined withdrawals (newest-first; stable by seq)
    withdrawals_combined = ledger_rows + req_rows
    withdrawals_combined.sort(key=lambda x: (x["created_at"], x["seq"]), reverse=True)
    ctx["withdrawals_combined"] = withdrawals_combined
    # -----------------------------------------------------------------------

    # === ONE merged stream for the "ALL" tab (newest-first across everything) ===
    all_rows = []

    # Normalize DB txns (they already include ledger withdrawals)
    for t in txns_all:
        kind_lc = (t.kind or "").lower()
        all_rows.append({
            "obj_id": t.id,                                  # <-- for modal
            "source": "txn",
            "kind_lc": kind_lc,
            "bucket": (t.bucket or "cash").lower(),
            "created_at": t.created_at,
            "amount_cents": int(t.amount_cents),
            "status": "confirmed",
            "seq": ("T", t.id),                              # tiebreaker
        })

    # Add request withdrawals (only requests to avoid duplicating ledger)
    for r in withdrawals_combined:
        if r["source"] == "request":
            all_rows.append({
                "obj_id": r["id"],                           # WithdrawalRequest id
                "source": "request",
                "kind_lc": "withdraw",
                "bucket": (r.get("bucket") or "cash").lower(),
                "created_at": r["created_at"],
                "amount_cents": int(r["amount_cents"]),
                "status": r.get("status"),
                "seq": r["seq"],                             # keep same tiebreaker
            })

    # Final newest-first sort (stable by seq)
    all_rows.sort(key=lambda x: (x["created_at"], x["seq"]), reverse=True)
    ctx["all_rows"] = all_rows
    # =======================================================================

    return render(request, "meta_search/wallet.html", {**ctx, "active_page": "wallet"})


def cents(d: Decimal) -> int:
    d = Decimal(str(d)).quantize(Decimal("0.01"))
    return int(d * 100)


def _symbol(code: str) -> str:
    return CURRENCY_SYMBOL.get(code, "€")

from decimal import Decimal, ROUND_HALF_UP

def cents(amount) -> int:
    return int((Decimal(str(amount)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))




#for fixed currency
def resolve_currency(method: str, selected_addr) -> str:

    #Pick a valid Currency value that actually exists on your Currency TextChoices.
    #Tries multiple common aliases and falls back safely.
    #Returns the *value* to store (e.g. 'ETH', 'USDT_TRC20', etc.).

    # Build a set of valid values and attribute names present on Currency
    # For Django TextChoices: members like Currency.ETH exist and their .value is the DB value.
    valid_attr_names = {m.name for m in Currency}           # e.g. {'EUR','ETH','USDT_TRC20'}
    valid_values      = {m.value for m in Currency}         # e.g. {'EUR','ETH','USDT_TRC20'}

    def first_existing(candidates):
        # Check by attribute name first (Currency.<NAME>), then by raw value
        for name in candidates:
            if name in valid_attr_names:
                return getattr(Currency, name).value
            if name in valid_values:
                return name
        return None

    # 1) Try from user-chosen method radio
    if method == "usdt_trc20":
        usdt_candidates = [
            "USDT_TRC20", "USDT_TRON", "USDTTRC20", "TRC20",
            "USDT", "TETHER"
        ]
        val = first_existing(usdt_candidates)
        if val:
            return val
    else:
        eth_candidates = ["ETH", "ETHEREUM", "ERC20"]
        val = first_existing(eth_candidates)
        if val:
            return val

    # 2) Try infer from selected address network (if available)
    if selected_addr and getattr(selected_addr, "network", None):
        net = (selected_addr.network or "").upper()
        if "TRC20" in net or "TRON" in net:
            val = first_existing(["USDT_TRC20", "USDT_TRON", "USDT"])
            if val:
                return val
        if "ETH" in net or "ERC20" in net or "ETHEREUM" in net:
            val = first_existing(["ETH", "ETHEREUM"])
            if val:
                return val

    # 3) Fallbacks: prefer ETH, else EUR, else the first enum value
    for fallback in ["ETH", "ETHEREUM", "EUR"]:
        val = first_existing([fallback])
        if val:
            return val

    # Absolute last resort: return *some* value
    return next(iter(valid_values))


"""
@login_required
def withdrawal(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    addresses = request.user.payout_addresses.order_by("-created_at")

    # Preselect an address from ?address=ID or fallback to the most recent verified one
    selected_id = request.GET.get("address")
    if selected_id:
        selected = addresses.filter(id=selected_id).first()
    else:
        selected = addresses.filter(is_verified=True).first()

    if request.method == "POST":
        form = WithdrawalForm(request.POST)
        if form.is_valid():
            amt = form.cleaned_data["amount"]

            # Derive a VALID currency that exists on your Currency enum
            method = request.POST.get("method")  # 'crypto' or 'usdt_trc20'
            currency_value = resolve_currency(method, selected)

            # Resolve address id (hidden field or selected)
            addr_id = form.cleaned_data.get("address_id") or (selected.id if selected else None)
            if not addr_id:
                messages.error(request, "Please add/select a payout address.")
                return redirect("withdrawal")

            address = get_object_or_404(PayoutAddress, id=addr_id, user=request.user)
            if not address.is_verified:
                messages.error(request, "Address not verified yet.")
                return redirect("withdrawal")

            amount_cents = cents(amt)
            if amount_cents > wallet.balance_cents:
                messages.error(request, "Insufficient balance.")
                return redirect("withdrawal")

            fee = WithdrawalForm.compute_fee(amt)

            WithdrawalRequest.objects.create(
                user=request.user,
                amount_cents=amount_cents,
                currency=currency_value,   # <- a real, valid Currency value
                address=address,
                fee_cents=cents(fee),
                status="pending",
            )

            wallet.pending_cents += amount_cents
            wallet.balance_cents -= amount_cents
            wallet.save()

            messages.success(request, "Withdrawal submitted.")
            return redirect("withdrawal_success")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = WithdrawalForm()

    ctx = {
        "form": form,
        "method_verified": any(a.is_verified for a in addresses),
        "addresses": addresses,
        "selected": selected,
        "wallet": wallet,
    }
    return render(request, "meta_search/withdrawal.html", ctx)

"""

from decimal import Decimal, ROUND_HALF_UP

def cents(amount) -> int:
    return int((Decimal(str(amount)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


from django.contrib.auth.decorators import login_required
from django.db import transaction

# your existing imports …
# from .forms import WithdrawalForm
# from .models import Wallet, WithdrawalRequest, PayoutAddress, Currency, cents
# ADD THIS:
from .models import ensure_task_progress  # STEP 2/3 need this

@never_cache
@require_http_methods(["GET", "POST"])
@login_required
def withdrawal(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    addresses = request.user.payout_addresses.order_by("-created_at")

    selected_id = request.GET.get("address")
    selected = (
        addresses.filter(id=selected_id).first()
        if selected_id else addresses.filter(is_verified=True).first()
    )

    # Fiat options
    currency_options = [{"value": c.value, "label": c.label} for c in Currency]
    currency_choices = [(o["value"], o["label"]) for o in currency_options]
    DEFAULT_FIAT = "EUR"

    if request.method == "POST":
        form = WithdrawalForm(request.POST)
        form.fields["currency"].choices = currency_choices

        # ---- One-time form token check (prevents back-button re-submit) ----
        posted_token = request.POST.get("wdw_token")
        session_token = request.session.pop("wdw_token", None)  # consume exactly once
        if not posted_token or not session_token or posted_token != session_token:
            messages.error(request, "This withdrawal form expired or was already submitted. Please start again.")
            return redirect("withdrawal")
        # -------------------------------------------------------------------

        if form.is_valid():
            amt = form.cleaned_data["amount"]
            fiat_currency = form.cleaned_data.get("currency") or DEFAULT_FIAT
            method = request.POST.get("method") or "crypto"

            addr_id = form.cleaned_data.get("address_id") or (selected.id if selected else None)
            if not addr_id:
                messages.error(request, "Please add/select a payout address.")
                return redirect("withdrawal")

            address = get_object_or_404(PayoutAddress, id=addr_id, user=request.user)
            if not address.is_verified:
                messages.error(request, "Address not verified yet.")
                return redirect("withdrawal")

            amount_cents = cents(amt)

            # ----- Withdrawal PIN checks -----
            tx_pin = (request.POST.get("tx_pin") or "").strip()
            if not request.user.has_tx_pin():
                messages.error(request, "Set your withdrawal password first.")
                return redirect("set_tx_pin")
            if not request.user.can_try_tx_pin():
                messages.error(request, "Too many incorrect attempts. Please try again later.")
                return redirect("withdrawal")
            if not tx_pin or not request.user.check_tx_pin(tx_pin):
                request.user.register_tx_pin_fail()
                messages.error(request, "Incorrect withdrawal password.")
                return redirect("withdrawal")
            request.user.register_tx_pin_success()
            # ---------------------------------

            # Resolve final currency value safely
            try:
                _ = [m.value for m in Currency if m.value == fiat_currency][0]
                currency_value = fiat_currency
            except IndexError:
                currency_value = resolve_currency(method, selected)

            # ----- Duplicate request guard (same details within 2 minutes) -----
            recent_window = timezone.now() - timedelta(minutes=2)
            if WithdrawalRequest.objects.filter(
                user=request.user,
                address=address,
                amount_cents=amount_cents,
                currency=currency_value,
                status__in=["pending", "awaiting_review", "processing", "confirmed"],
                created_at__gte=recent_window,
            ).exists():
                messages.info(request, "We already received a similar withdrawal a moment ago.")
                return redirect("withdrawal_success")
            # -------------------------------------------------------------------

            # ======================= STEP 2: gate by cycles =======================
            prog = ensure_task_progress(request.user)
            ok, why_not = prog.can_withdraw()
            if not ok:
                messages.error(request, why_not or "Withdrawals are not available yet.")
                return redirect("withdrawal")
            # =====================================================================

            # ----- Atomic wallet update + request creation -----
            with transaction.atomic():
                # lock wallet row to avoid race/double spend
                w = Wallet.objects.select_for_update().get(user=request.user)
                if amount_cents > w.balance_cents:
                    messages.error(request, "Insufficient balance.")
                    return redirect("withdrawal")

                fee = WithdrawalForm.compute_fee(amt)

                WithdrawalRequest.objects.create(
                    user=request.user,
                    amount_cents=amount_cents,
                    currency=currency_value,
                    address=address,
                    fee_cents=cents(fee),
                    status="pending",
                )

                w.pending_cents += amount_cents
                w.balance_cents -= amount_cents
                w.save()

                # ===================== STEP 3: record the cycle =====================
                # If you prefer to mark only on *confirmed* payout, move this line to
                # whatever handler flips status to "confirmed".
                prog.mark_withdraw_done()
                # ====================================================================

            messages.success(request, "Withdrawal submitted.")
            return redirect("withdrawal_success")
        else:
            messages.error(request, "Please correct the errors below.")

    else:
        form = WithdrawalForm(initial={"currency": DEFAULT_FIAT})
        form.fields["currency"].choices = currency_choices

        # ---- Issue a fresh one-time token on every GET ----
        token = secrets.token_urlsafe(20)
        request.session["wdw_token"] = token
        wdw_token = token
        # ---------------------------------------------------

    # Build context ONCE (no duplicates)
    ctx = {
        "form": form,
        "method_verified": any(a.is_verified for a in addresses),
        "addresses": addresses,
        "selected": selected,
        "wallet": wallet,
        "currency_options": currency_options,
        "fiat_symbol": {"EUR": "€", "USD": "$", "GBP": "£"}.get(form["currency"].value() or DEFAULT_FIAT, "€"),
        "has_tx_pin": request.user.has_tx_pin(),
        "wdw_token": locals().get("wdw_token", ""),  # for the template hidden input
        # optional: keep the chosen method sticky in the UI
        "method": request.POST.get("method") if request.method == "POST" else "crypto",
    }

    # Add the policy flags for the template
    prog = ensure_task_progress(request.user)
    ok, why_not = prog.can_withdraw()

    # Has the user ever withdrawn? Prefer progress field set by prog.mark_withdraw_done()
    has_withdrawn_before = bool(getattr(prog, "last_withdraw_cycle", 0))

    # Fallback to history lookup if you need it (uncomment if desired and you have the import):
    # if not has_withdrawn_before:
    #     has_withdrawn_before = WithdrawalRequest.objects.filter(
    #         user=request.user,
    #         status__in=["pending", "awaiting_review", "processing", "confirmed"]
    #     ).exists()

    ctx.update({
        "withdraw_locked": not ok,
        "withdraw_reason": why_not or "",
        "withdraw_cycles_remaining": getattr(prog, "cycles_left_after_last_withdraw", None),
        # 👇 show the one-time policy popup ONLY if they have withdrawn before and are currently locked
        "show_withdraw_policy_popup": has_withdrawn_before and (not ok),
    })

    return render(request, "meta_search/withdrawal.html", ctx)



@login_required
@never_cache
def add_address(request):
    """
    Replace/update flow, resilient to existing duplicates.
    Keeps the newest record for (user, address_type) and deletes the rest,
    then updates that survivor.
    """
    if request.method == "POST":
        form = AddressForm(request.user, request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                with transaction.atomic():
                    # Lock all rows for this user+network to avoid races
                    qs = (PayoutAddress.objects
                          .select_for_update()
                          .filter(user=request.user, address_type=cd["address_type"])
                          .order_by("-created_at", "-id"))

                    if qs.exists():
                        # Keep the newest as primary; delete other duplicates
                        primary = qs.first()
                        dup_ids = list(qs.values_list("pk", flat=True))[1:]
                        if dup_ids:
                            PayoutAddress.objects.filter(pk__in=dup_ids).delete()

                        # Update the survivor
                        primary.address = cd["address"]
                        primary.label = cd.get("label", "") or ""
                        primary.is_verified = True
                        primary.save()
                        addr, created = primary, False
                    else:
                        # No existing — create fresh
                        addr = PayoutAddress.objects.create(
                            user=request.user,
                            address_type=cd["address_type"],
                            address=cd["address"],
                            label=cd.get("label", "") or "",
                            is_verified=True,
                        )
                        created = True

                messages.success(request, "Payout address added." if created else "Payout address updated.")
                nxt = request.GET.get("next") or reverse("withdrawal")
                return redirect(f"{nxt}?address={addr.id}")

            except IntegrityError:
                messages.error(request, "Could not save your payout address due to a conflict. Please try again.")
    else:
        form = AddressForm(request.user)

    return render(request, "meta_search/address_add.html", {"form": form})

@never_cache
@login_required
def withdrawal_success(request):
    return render(request, "meta_search/withdrawal_success.html")

# -----------------------------
# Deposit
# -----------------------------
@login_required
@never_cache
def deposit(request):
    """Deposit: amount + currency + network + preset chips."""
    if request.method == "POST":
        form = DepositForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            currency = form.cleaned_data["currency"]
            network = form.cleaned_data["network"]

            pay_to = DepositAddress.objects.filter(network=network, active=True).first()
            if not pay_to:
                messages.error(request, "Payment address for the selected network is not available.")
                return redirect("deposit")

            dep = DepositRequest.objects.create(
                user=request.user,
                amount_cents=cents(amount),
                currency=currency,
                network=network,
                pay_to=pay_to,
                status="awaiting_payment",
                reference=DepositRequest.new_reference(),
            )
            return redirect("deposit_pay", pk=dep.id)
    else:
        form = DepositForm()

    symbol = _symbol(form.initial.get("currency", "EUR"))
    presets = [10, 20, 30, 50, 100, 300, 500, 1000]  # ✅ use this in template

    return render(
        request,
        "meta_search/deposit.html",
        {"form": form, "symbol": symbol, "presets": presets},
    )


@login_required
@never_cache
def deposit_pay(request, pk):
    """Payment page with 20s TTL + Telegram escalation."""
    dep = get_object_or_404(DepositRequest, pk=pk, user=request.user)
    payload = dep.pay_to.address

    # QR code as data URI
    qr_data_uri = None
    try:
        import qrcode
        img = qrcode.make(payload)
        buf = BytesIO(); img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        qr_data_uri = f"data:image/png;base64,{qr_b64}"
    except Exception:
        qr_data_uri = None

    support_telegram_url = getattr(settings, "SUPPORT_TELEGRAM_URL", "https://t.me/benchatronics")

    # ---- TTL: force 20 seconds (for 20s) ----
    ttl_seconds = 20

    waited_seconds = None
    show_telegram_now = False
    if dep.status == "awaiting_review" and dep.verified_at:
        delta = timezone.now() - dep.verified_at
        waited_seconds = int(delta.total_seconds())
        show_telegram_now = waited_seconds >= ttl_seconds

    return render(request, "meta_search/deposit_pay.html", {
        "dep": dep,
        "symbol": _symbol(dep.currency),
        "qr_data_uri": qr_data_uri,
        "support_telegram_url": support_telegram_url,
        "telegram_ttl_seconds": ttl_seconds,  # JS reads this
        "show_telegram_now": show_telegram_now,
    })


@login_required
def deposit_status(request, pk):
    """Lightweight status poller for the pay page."""
    dep = get_object_or_404(DepositRequest, pk=pk, user=request.user)
    return JsonResponse({
        "status": dep.status,
        "verified_at": dep.verified_at.isoformat() if dep.verified_at else None,
    })



@login_required
def deposit_verify(request, pk):
    """User clicks Verify after paying—mark as awaiting_review for staff/on-chain check."""
    dep = get_object_or_404(DepositRequest, pk=pk, user=request.user)
    if request.method == "POST" and dep.status == "awaiting_payment":
        dep.status = "awaiting_review"
        dep.verified_at = timezone.now()
        dep.save()
        messages.success(request, "Thanks! We’ll verify the payment shortly.")
    return redirect("deposit_pay", pk=dep.id)


@staff_member_required
def deposit_admin_confirm(request, pk):
    dep = get_object_or_404(DepositRequest, pk=pk)
    did_confirm = confirm_deposit(dep)
    if did_confirm:
        messages.success(request, f"Deposit {dep.reference} confirmed and wallet credited.")
    else:
        messages.info(request, f"Deposit {dep.reference} is already {dep.status}.")
    return redirect(reverse("admin:wallet_depositrequest_change", args=[dep.id]))


from typing import Optional
import base64, hashlib, hmac

def _signature_valid(secret: str, raw_body: bytes, sig_header: Optional[str]) -> bool:
    if not sig_header:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    ).decode()
    # normalize (optional) if incoming header might have spaces/newlines
    return hmac.compare_digest(sig_header.strip(), expected)


"""
# -----------------------------
# Deposit webhook (TWO-MODE, single definition)
# -----------------------------
def _signature_valid(secret: str, raw_body: bytes, sig_header: str | None) -> bool:
    if not sig_header:
        return False
    expected = base64.b64encode(hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(sig_header, expected)
"""

@csrf_exempt
def deposit_webhook_confirm(request):
    """
    Two-mode webhook:
      - If settings.DEPOSIT_WEBHOOK_SECRET is set => requires X-DEP-SIGN HMAC header
      - If not set => insecure mode (for local testing ONLY)
    Expects JSON: {"reference":"...", "network":"ETH|TRC20", "txid":"...", "amount":"100.00"}
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Only POST allowed"}, status=405)

    raw = request.body
    try:
        data = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    # Secure mode if secret is present
    secret = getattr(settings, "DEPOSIT_WEBHOOK_SECRET", None)
    if secret:
        sig = request.headers.get("X-DEP-SIGN")
        if not _signature_valid(secret, raw, sig):
            return JsonResponse({"ok": False, "error": "Invalid or missing signature"}, status=403)

    reference = data.get("reference")
    txid = data.get("txid")
    # Optional strict checks:
    # if data.get("network") != dep.network: ...
    # if Decimal(data.get("amount","0")) != Decimal(dep.amount): ...

    if not reference:
        return JsonResponse({"ok": False, "error": "Missing 'reference'"}, status=400)

    dep = get_object_or_404(DepositRequest, reference=reference)

    # (Optional) store txid if provided
    if txid and getattr(dep, "txid", None) != txid:
        dep.txid = txid
        dep.save(update_fields=["txid"])

    confirmed_now = confirm_deposit(dep)
    return JsonResponse({"ok": True, "confirmed": confirmed_now, "status": dep.status})


#language
def language_settings(request):
    return render(request, "meta_search/language.html", {
        "active_page": "settings",
        "current_tab": "language",
        "LANGUAGES": settings.LANGUAGES,   # my i18n.py list
        "LANGUAGE_CODE": get_language(),   # current user language
    })


@csrf_protect
@login_required
def settings_change_password(request):
    """
    Fully dynamic change-password:
    - country_code + phone_number (must match account)
    - captcha (django-simple-captcha)
    - old_password / new_password1 / new_password2
    - messages + ?next redirect (same pattern as signin/signup)
    """
    redirect_to = request.GET.get('next') or request.POST.get('next') or 'settings_change_password'

    if request.method == 'POST':
        form = ChangePasswordForm(user=request.user, data=request.POST, request=request)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # keep session
            messages.success(request, _("Password updated."))
            return redirect(redirect_to)
        messages.error(request, _("Please correct the errors below."))
    else:
        form = ChangePasswordForm(user=request.user, request=request)

    return render(request, 'meta_search/settings_change_password.html', {
        'form': form,
        'next': redirect_to,
        'active_page': 'settings',
        'current_tab': 'change_password',
    })


"""
def info_page(request, key):
    page = get_object_or_404(InfoPage, key=key, is_published=True)
    # Show a small stack of current announcements at the top of every info page
    announces = Announcement.objects.active()[:5]
    return render(request, "meta_search/info_page.html", {
        "page": page,
        "announces": announces,
        "active_page": "info",  #sidebar highlights this
    })
"""


def announcements_list(request):
    items = Announcement.objects.active()
    return render(request, "meta_search/announcements.html", {
        "items": items,
        "active_page": "info",
    })


INFO_KEYS = {"about", "contact", "help", "level", "signin_reward"}

def _announces_top3():
    # Works whether you added a custom .active() manager or not
    try:
        return Announcement.objects.active()[:3]
    except Exception:
        return Announcement.objects.order_by("-created_at")[:3]

@login_required
def info_index(request):
    """
    Clicking 'Info' should open 'About us' by default.
    We simply redirect to /info/about/ so base_info.html renders with the left menu active.
    """
    return redirect("info_page", key="about")

@login_required
def info_page(request, key: str):
    """
    Render a single Info page (About, Contact, Help, Level, Sign-in reward)
    inside base_info.html. Left menu is handled by the template; we pass
    `current_info` to light up the active item.
    """
    page = get_object_or_404(InfoPage, key=key, is_published=True)
    announces = _announces_top3()

    # Only highlight known left-menu items; other keys still render but won't highlight.
    current_info = key if key in INFO_KEYS else None

    return render(request, "meta_search/info_page.html", {
        "page": page,
        "announces": announces,
        "active_page": "info",   # highlights 'Info' in base_user sidebar (if you use it)
        "current_info": current_info,
    })



