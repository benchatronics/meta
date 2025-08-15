# main/views.py
import random
import base64
import hmac
import hashlib
import json
from decimal import Decimal
from io import BytesIO

import phonenumbers
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Value, BooleanField
from django.http import (
    JsonResponse,
    HttpResponse,
)
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_protect, csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_date

from .country_codes import COUNTRY_CODES
from .forms import (
    SignupForm, LoginForm, StaffResetPasswordForm,
    WithdrawalForm, AddressForm, DepositForm, CURRENCY_SYMBOL
)
from .models import (
    Wallet, PayoutAddress, WithdrawalRequest, AddressType, Currency,
    DepositAddress, DepositRequest, Network,
    Hotel, Favorite
)
from .services import confirm_deposit


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


def _send_sms(phone_e164: str, message: str) -> None:
    # TODO: integrate real SMS provider
    print(f"[SMS to {phone_e164}] {message}")  # dev placeholder


def _generate_otp() -> str:
    return f"{random.randint(100000, 999999)}"


User = get_user_model()


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

            otp = _generate_otp()
            cache.set(f"pr_otp:{phone}", otp, timeout=600)  # 10 minutes
            _send_sms(phone, _("Your reset code is: %(otp)s. It expires in 10 minutes.") % {"otp": otp})

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
def index(request):
    """Homepage."""
    return render(request, 'meta_search/index.html')


@csrf_protect
def signin(request):
    """
    Phone-based login using LoginForm (country_code + phone_number + password).
    - Validates & normalizes phone to E.164 in the form.
    - Supports ?next=/path for post-login redirect.
    """
    redirect_to = request.GET.get('next') or request.POST.get('next') or 'index'

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


def signup_view(request):
    """
    Signup view passing request into the form (for IP & country capture).
    Auto-logs in after successful signup.
    """
    redirect_to = request.GET.get('next') or request.POST.get('next') or 'index'

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


# -----------------------------
# Dashboard
# -----------------------------
@login_required
def user_dashboard(request):
    """
    Render dashboard with three tabs.

    Supported GET params:
      - location: filters by Hotel.city (case-insensitive exact)
      - date:     filters by Hotel.available_date (YYYY-MM-DD)
      - rating:   filters by Hotel.score >= value (only for Rating tab)
      - tab:      optional, 'recommended' | 'popular' | 'rating' to keep active tab
    """
    user = request.user

    # Incoming filters
    rating_param   = (request.GET.get("rating") or "").strip()
    location_param = (request.GET.get("location") or "").strip()
    date_param     = (request.GET.get("date") or "").strip()

    # Decide active tab (prefer Rating when rating filter is present)
    active_tab = request.GET.get("tab") or ("rating" if rating_param else "recommended")

    # Check if favorited (per card)
    fav_subq = Favorite.objects.filter(user=user, hotel=OuterRef("pk"))

    # Base queryset (shared by all tabs)
    base = (
        Hotel.objects.filter(is_published=True)
        .select_related("country")
        .annotate(
            is_favorited=Exists(fav_subq),
            favorites_total=Count("favorites", distinct=True),
        )
    )

    # Apply Location and Date to ALL tabs
    if location_param:
        base = base.filter(city__iexact=location_param)
    if date_param:
        d = parse_date(date_param)
        if d:
            base = base.filter(available_date=d)

    # Rating tab extra filter
    rating_qs = base
    if rating_param:
        try:
            rating_value = float(rating_param)
            rating_qs = rating_qs.filter(score__gte=rating_value)
        except ValueError:
            pass

    # Build tab querysets
    recommended_hotels = base.filter(is_recommended=True).order_by("-created_at", "-score", "name")[:12]
    popular_hotels     = base.order_by("-popularity", "-created_at", "name")[:12]
    rating_hotels      = rating_qs.order_by("-score", "-created_at", "name")[:12]

    context = {
        "hotel_tabs": [
            ("recommended", recommended_hotels),
            ("popular", popular_hotels),
            ("rating", rating_hotels),
        ],
        "active_tab": active_tab,
    }
    return render(request, "meta_search/user_dashboard.html", context)


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
def wallet_view(request):
    return render(request, "meta_search/wallet.html", {"active_page": "wallet"})


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

@login_required
def withdrawal(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    addresses = request.user.payout_addresses.order_by("-created_at")

    selected_id = request.GET.get("address")
    if selected_id:
        selected = addresses.filter(id=selected_id).first()
    else:
        selected = addresses.filter(is_verified=True).first()

    # Build fiat currency list (EUR/USD/GBP) for the form and template
    currency_options = [{"value": c.value, "label": c.label} for c in Currency]
    currency_choices = [(o["value"], o["label"]) for o in currency_options]
    DEFAULT_FIAT = "EUR"

    if request.method == "POST":
        form = WithdrawalForm(request.POST)
        form.fields["currency"].choices = currency_choices

        if form.is_valid():
            amt = form.cleaned_data["amount"]

            # user-chosen fiat currency (EUR/USD/GBP) – may be empty if the user never touched it
            fiat_currency = form.cleaned_data.get("currency") or DEFAULT_FIAT

            # keep your existing method read (unchanged), e.g. 'crypto' / 'usdt_trc20' or 'ETH'/'TRC20'
            method = request.POST.get("method")

            # Your existing hidden selection logic
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

            # Store the user's fiat selection in WithdrawalRequest.currency.
            # If for any reason it's missing from the enum, fall back to your resolver.
            try:
                # make sure fiat_currency is a valid enum value
                _ = [m.value for m in Currency if m.value == fiat_currency][0]
                currency_value = fiat_currency
            except IndexError:
                currency_value = resolve_currency(method, selected)

            WithdrawalRequest.objects.create(
                user=request.user,
                amount_cents=amount_cents,
                currency=currency_value,   # now EUR/USD/GBP when user chooses it
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
        form = WithdrawalForm(initial={"currency": DEFAULT_FIAT})
        form.fields["currency"].choices = currency_choices

    ctx = {
        "form": form,
        "method_verified": any(a.is_verified for a in addresses),
        "addresses": addresses,
        "selected": selected,
        "wallet": wallet,
        "currency_options": currency_options,
        "fiat_symbol": {"EUR":"€","USD":"$","GBP":"£"}.get(form["currency"].value() or DEFAULT_FIAT, "€"),
    }
    return render(request, "meta_search/withdrawal.html", ctx)


@login_required
def add_address(request):
    """Create ETH/TRC20 payout address; returns to withdrawal with it preselected."""
    if request.method == "POST":
        form = AddressForm(request.POST)
        if form.is_valid():
            addr = form.save(commit=False)
            addr.user = request.user
            addr.save()
            messages.success(request, "Payout address added.")
            nxt = request.GET.get("next") or reverse("withdrawal")
            return redirect(f"{nxt}?address={addr.id}")
    else:
        form = AddressForm()
    return render(request, "meta_search/address_add.html", {"form": form})


@login_required
def withdrawal_success(request):
    return render(request, "meta_search/withdrawal_success.html")

# -----------------------------
# Deposit
# -----------------------------
@login_required
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
def deposit_pay(request, pk):
    """Payment page: shows QR, address, amount, reference, and verify button."""
    dep = get_object_or_404(DepositRequest, pk=pk, user=request.user)
    payload = dep.pay_to.address

    # QR code as data URI
    qr_data_uri = None
    try:
        import qrcode
        img = qrcode.make(payload)
        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        qr_data_uri = f"data:image/png;base64,{qr_b64}"
    except Exception:
        qr_data_uri = None

    return render(request, "deposit_pay.html", {
        "dep": dep,
        "symbol": _symbol(dep.currency),
        "qr_data_uri": qr_data_uri,
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


# -----------------------------
# Deposit webhook (TWO-MODE, single definition)
# -----------------------------
def _signature_valid(secret: str, raw_body: bytes, sig_header: str | None) -> bool:
    if not sig_header:
        return False
    expected = base64.b64encode(hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(sig_header, expected)


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
