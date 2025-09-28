# main/forms.py
from __future__ import annotations
from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm, ReadOnlyPasswordHashField
from django.utils.translation import gettext_lazy as _
from captcha.fields import CaptchaField
import phonenumbers
from urllib.parse import urlparse
from django.core.files.uploadedfile import UploadedFile
import requests
from django.contrib.auth.forms import PasswordChangeForm
from decimal import Decimal
from .models import PayoutAddress, AddressType, Currency, Network
from .constants import MIN_EUR, MAX_EUR, FEE_PCT, FEE_FIXED_EUR
from .models import CustomUser
from .country_codes import COUNTRY_CODES  # (dial, display, min_len, max_len)
from django.core.exceptions import ValidationError
import re


from django import forms
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.db.models import Q, F
from django.utils import timezone
import phonenumbers
from .models import InvitationLink


class SignupForm(UserCreationForm):
    country_code = forms.ChoiceField(
        choices=[(dial, disp) for dial, disp, _, _ in COUNTRY_CODES],
        widget=forms.Select(attrs={'class': 'country-select'}),
        label=_("Country code"),
    )
    phone_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={
            'placeholder': _("Enter your phone number"),
            'autocomplete': 'tel',
            'class': 'cc-input'
        }),
        label=_("Phone number"),
    )

    # REQUIRED now; placeholder says "Enter your invitation link" as requested
    invitation_code = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': _("Enter your invitation link"),
            'class': 'text-input'
        }),
        label=_("Invitation code"),
    )

    captcha = CaptchaField(label=_("I am not a robot"))

    # stash normalized code string
    invite_code_str: str | None = None

    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ('country_code', 'phone_number', 'invitation_code', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        # accept request so we can capture signup IP + country
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({
            'placeholder': _("Enter your password"),
            'class': 'pwd-input'
        })
        self.fields['password2'].widget.attrs.update({
            'placeholder': _("Re-enter your password"),
            'class': 'pwd-input'
        })

    # ---------- Phone helpers ----------
    def _normalize_to_e164(self, country_dial: str, raw: str) -> str:
        cleaned = []
        for ch in raw.strip():
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

    # ---------- Invitation code validation (precheck ONLY) ----------
    def clean_invitation_code(self):
        raw = (self.cleaned_data.get("invitation_code") or "").strip()
        if not raw:
            raise forms.ValidationError(_("Invitation code is required."))

        code_str = raw.replace(" ", "").upper()  # normalize (no spaces, case-insensitive)

        # Use your model’s helper for a light precheck (no locks)
        if not InvitationLink.can_be_used(code_str):
            raise forms.ValidationError(_("Invalid, used, expired, or suspended invitation code."))

        self.invite_code_str = code_str
        return raw  # keep original text in the input

    # ---------- Cross-field clean (phone) ----------
    def clean(self):
        cleaned_data = super().clean()
        dial = cleaned_data.get("country_code")
        raw = cleaned_data.get("phone_number")

        if not dial or not raw:
            return cleaned_data

        disp, lo, hi = _DIAL_RULES.get(dial, (None, None, None))
        local_digits = ''.join(ch for ch in raw if ch.isdigit())
        if lo and hi and not (lo <= len(local_digits) <= hi):
            self.add_error(
                'phone_number',
                _("Phone number length must be between %(lo)d and %(hi)d digits for %(country)s.")
                % {"lo": lo, "hi": hi, "country": disp}
            )
            return cleaned_data

        try:
            normalized = self._normalize_to_e164(dial, raw)
        except forms.ValidationError as e:
            self.add_error('phone_number', e)
            return cleaned_data

        cleaned_data["phone"] = normalized

        if CustomUser.objects.filter(phone=normalized).exists():
            self.add_error('phone_number', _("An account with this phone already exists."))

        return cleaned_data

    # ---------- Save (atomic single-use claim) ----------
    @transaction.atomic
    def save(self, commit=True):
        """
        1) Atomically mark the invitation as claimed (single use).
        2) Create the user.
        3) Attach invite, set used_by/used_at.
        If anything fails, the transaction rolls back (claim is undone).
        """
        code = (self.invite_code_str or (self.cleaned_data.get('invitation_code') or '')).replace(" ", "").upper()
        now = timezone.now()

        # Step 1: Claim atomically (single-use)
        claimed = (InvitationLink.objects
                   .filter(
                       code__iexact=code,
                       is_active=True,
                       claimed=False,
                       used_by__isnull=True,
                   )
                   .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                   .update(claimed=True))
        if claimed == 0:
            # Another request took it, or it got invalidated between clean() and save()
            raise ValidationError(_("This invitation code is no longer available. Please contact support."))

        # Load the invite row we just claimed
        invite = InvitationLink.objects.select_for_update().get(code__iexact=code)

        # Step 2: Create user (your existing behavior)
        user = super().save(commit=False)
        user.phone = self.cleaned_data.get('phone') or f"{self.cleaned_data['country_code']}{''.join(ch for ch in self.cleaned_data['phone_number'] if ch.isdigit())}"
        user.username = user.phone

        # If your CustomUser has a place to store the raw code, keep it
        if hasattr(user, "invitation_code"):
            user.invitation_code = invite.code

        # If you track who invited them
        if hasattr(user, "invited_by") and invite.owner_id:
            user.invited_by = invite.owner

        # Capture signup IP & country (your existing helpers)
        ip = _client_ip_from_request(self.request)
        if ip:
            if hasattr(user, "signup_ip"):
                user.signup_ip = ip
            if hasattr(user, "signup_country"):
                user.signup_country = _country_from_ip(ip)

        if commit:
            user.save()

        # Step 3: Mark as used by this user
        invite.used_by = user
        invite.used_at = now
        invite.save(update_fields=["used_by", "used_at"])

        return user




#withdrawal password scope
PIN_RE = re.compile(r"^\d{6}$")  # 6 digits

class SetTxPinForm(forms.Form):
    account_password = forms.CharField(
        label=_("Account password"),
        widget=forms.PasswordInput(attrs={"autocomplete":"current-password"})
    )
    tx_pin1 = forms.CharField(
        label=_("Withdrawal password (6 digits)"),
        widget=forms.PasswordInput(attrs={"inputmode":"numeric","pattern":"\\d{6}","maxlength":"6"})
    )
    tx_pin2 = forms.CharField(
        label=_("Confirm withdrawal password"),
        widget=forms.PasswordInput(attrs={"inputmode":"numeric","pattern":"\\d{6}","maxlength":"6"})
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_account_password(self):
        pwd = self.cleaned_data["account_password"]
        if not self.user or not self.user.check_password(pwd):
            raise forms.ValidationError(_("Incorrect account password."))
        return pwd

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("tx_pin1"), cleaned.get("tx_pin2")
        if p1 and not PIN_RE.match(p1):
            self.add_error("tx_pin1", _("Enter exactly 6 digits."))
        if p2 and p1 != p2:
            self.add_error("tx_pin2", _("Passwords do not match."))
        return cleaned


class ChangeTxPinForm(forms.Form):
    old_tx_pin = forms.CharField(
        label=_("Current withdrawal password"),
        widget=forms.PasswordInput(attrs={"inputmode":"numeric","pattern":"\\d{6}","maxlength":"6"})
    )
    new_tx_pin1 = forms.CharField(
        label=_("New withdrawal password (6 digits)"),
        widget=forms.PasswordInput(attrs={"inputmode":"numeric","pattern":"\\d{6}","maxlength":"6"})
    )
    new_tx_pin2 = forms.CharField(
        label=_("Confirm new withdrawal password"),
        widget=forms.PasswordInput(attrs={"inputmode":"numeric","pattern":"\\d{6}","maxlength":"6"})
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_tx_pin(self):
        pin = self.cleaned_data["old_tx_pin"]
        if not self.user or not self.user.check_tx_pin(pin):
            raise forms.ValidationError(_("Incorrect withdrawal password."))
        return pin

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("new_tx_pin1"), cleaned.get("new_tx_pin2")
        if p1 and not PIN_RE.match(p1):
            self.add_error("new_tx_pin1", _("Enter exactly 6 digits."))
        if p2 and p1 != p2:
            self.add_error("new_tx_pin2", _("Passwords do not match."))
        return cleaned






# Helper: map dial -> (display, min_len, max_len)
_DIAL_RULES = {dial: (disp, lo, hi) for dial, disp, lo, hi in COUNTRY_CODES}

def _client_ip_from_request(request):
    """Return best-guess client IP, supporting proxies."""
    if not request:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')

def _country_from_ip(ip):
    """
    Free fallback: get country name via ipapi.co (no account needed).
    Returns None if unavailable.
    """
    if not ip:
        return None
    try:
        r = requests.get(f"https://ipapi.co/{ip}/country_name/", timeout=3)
        if r.ok:
            name = (r.text or "").strip()
            return name or None
    except requests.RequestException:
        pass
    return None


class AdminUserCreationForm(UserCreationForm):
    class Meta:
        model = CustomUser
        fields = "__all__"   # or just ("phone",) if you prefer

class AdminUserChangeForm(UserChangeForm):
    class Meta:
        model = CustomUser
        fields = "__all__"   # ensures nickname/avatar fields are available


# ---- Upload guardrails ----
ALLOWED_IMG_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_AVATAR_MB = 4  # change if you want

#profile update
class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ["nickname", "avatar", "avatar_url"]
        widgets = {
            "nickname": forms.TextInput(attrs={
                "placeholder": "Your nickname",
                "autocomplete": "nickname",
            }),
            "avatar": forms.ClearableFileInput(attrs={
                "accept": "image/*",
            }),
            "avatar_url": forms.URLInput(attrs={
                "placeholder": "https://example.com/photo.jpg",
                "inputmode": "url",
                "autocomplete": "url",
            }),
        }
        help_texts = {
            "avatar": _("JPG/PNG/WebP/GIF, up to %(mb)s MB.") % {"mb": MAX_AVATAR_MB},
            "avatar_url": _("Direct image URL (used only if no file is uploaded)."),
        }

    def clean_avatar(self):
        f = self.cleaned_data.get("avatar")

        # No new file selected → keep existing value, no validation
        if not f:
            return f

        # If it's NOT an UploadedFile, it's the existing FieldFile from the instance.
        # Skip type/size checks in that case.
        if not isinstance(f, UploadedFile):
            return f

        # New upload: validate type and size
        ctype = (getattr(f, "content_type", "") or "").split(";")[0].strip()
        if ctype not in ALLOWED_IMG_TYPES:
            raise ValidationError(_("Unsupported image type. Use JPG, PNG, WebP, or GIF."))
        if f.size and f.size > MAX_AVATAR_MB * 1024 * 1024:
            raise ValidationError(_("Image too large (max %(mb)s MB).") % {"mb": MAX_AVATAR_MB})
        return f

    def clean_avatar_url(self):
        url = (self.cleaned_data.get("avatar_url") or "").strip()
        if not url:
            return ""
        parts = urlparse(url)
        if parts.scheme not in ("http", "https"):
            raise ValidationError(_("Only http/https URLs are allowed."))
        return url

    def clean(self):
        cleaned = super().clean()
        # If both provided, prefer uploaded file and clear URL
        if isinstance(cleaned.get("avatar"), UploadedFile) and cleaned.get("avatar_url"):
            cleaned["avatar_url"] = ""
        return cleaned


# ===== Admin forms (updated) =====
class AdminUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ("phone",)

class AdminUserChangeForm(UserChangeForm):
    # This makes Admin show a read-only hash and the "Change password" link
    password = ReadOnlyPasswordHashField(
        label=_("Password"),
        help_text=_(
            "Raw passwords are not stored, so there is no way to see this user’s password, "
            "but you can change the password using the “Change password” form."
        ),
    )

    class Meta:
        model = CustomUser
        fields = (
            "phone",
            "password",           # IMPORTANT: keep this here
            "invitation_code",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )

    def clean_password(self):
        # Always return the initial hash; we never edit it directly in this form.
        return self.initial.get("password")


# ===== Staff-only password reset form (by phone) =====
class StaffResetPasswordForm(forms.Form):
    phone = forms.CharField(
        max_length=32,
        label=_("User phone (E.164 or local NG)"),
        widget=forms.TextInput(attrs={"placeholder": _("+2348012345678 or 080...")}),
    )
    new_password1 = forms.CharField(widget=forms.PasswordInput, label=_("New password"))
    new_password2 = forms.CharField(widget=forms.PasswordInput, label=_("Confirm new password"))

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("new_password1"), cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password2", _("Passwords do not match."))
        return cleaned


# ===== Login form (phone + password) =====
class LoginForm(forms.Form):
    country_code = forms.ChoiceField(
        choices=[(dial, disp) for dial, disp, _, _ in COUNTRY_CODES],
        widget=forms.Select(attrs={'class': 'country-select'}),
        label=_("Country code"),
    )
    phone_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={
            'placeholder': _("Enter your phone number"),
            'autocomplete': 'tel',
            'class': 'cc-input'
        }),
        label=_("Phone number"),
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(attrs={
            'placeholder': _("Enter your password"),
            'class': 'pwd-input',
            'id': 'password'  # for the eye toggle hook in the template
        }),
        label=_("Password"),
    )

    def _normalize_to_e164(self, country_dial: str, raw: str) -> str:
        """Duplicate of SignupForm normalization so we don't modify existing code."""
        cleaned = []
        for ch in raw.strip():
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

    def clean(self):
        cleaned = super().clean()
        dial = cleaned.get("country_code")
        raw = cleaned.get("phone_number")
        if not dial or not raw:
            return cleaned
        # Optional length hint from your COUNTRY_CODES map
        disp, lo, hi = _DIAL_RULES.get(dial, (None, None, None))
        local_digits = ''.join(ch for ch in raw if ch.isdigit())
        if lo and hi and not (lo <= len(local_digits) <= hi):
            self.add_error('phone_number', _("Phone number length must be between %(lo)d and %(hi)d digits for %(country)s.") % {"lo": lo, "hi": hi, "country": disp})
            return cleaned

        try:
            normalized = self._normalize_to_e164(dial, raw)
        except forms.ValidationError as e:
            self.add_error('phone_number', e)
            return cleaned

        # Store normalized phone in a canonical key 'phone' so the view can use it to authenticate
        cleaned["phone"] = normalized
        return cleaned



# --- Withdrawal ---
MIN_EUR = 10
MAX_EUR = 5000
FEE_PCT = Decimal("0.015")
FEE_FIXED_EUR = Decimal("1.00")


ETH_RE = re.compile(r'^0x[a-fA-F0-9]{40}$')
TRC20_RE = re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$')
class AddressForm(forms.ModelForm):
    class Meta:
        model = PayoutAddress
        fields = ["address_type", "address", "label"]

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user  # needed to assign on save

    def clean_address(self):
        addr_type = self.cleaned_data.get("address_type")
        addr = (self.cleaned_data.get("address") or "").strip()

        if addr_type == AddressType.ETH:
            if not ETH_RE.match(addr):
                raise forms.ValidationError("Invalid Ethereum address (must start with 0x and be 42 characters).")
            # normalize ETH to lowercase after 0x
            addr = '0x' + addr[2:].lower()

        elif addr_type == AddressType.TRC20:
            if not TRC20_RE.match(addr):
                raise forms.ValidationError("Invalid TRC20 address (starts with T and is 34 characters).")

        return addr

    def clean(self):
        # No duplicate check here — the view will replace/update existing entry.
        return super().clean()

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.user = self.user
        # optional: keep normalization here too, if model.save doesn't already do it
        if obj.address_type == AddressType.ETH and obj.address.startswith('0x') and len(obj.address) == 42:
            obj.address = '0x' + obj.address[2:].lower()
        if commit:
            obj.save()
        return obj


"""
#fixed currency to euro
class WithdrawalForm(forms.Form):
    amount = forms.DecimalField(min_value=0, decimal_places=2, max_digits=12)

    # Make currency non-blocking here; we'll compute/validate it in the view.
    currency = forms.CharField(required=False)

    address_id = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount") or Decimal("0")
        if amount < MIN_EUR or amount > MAX_EUR:
            raise forms.ValidationError(
                f"Amount must be between €{MIN_EUR} and €{MAX_EUR}."
            )
        return cleaned

    @staticmethod
    def compute_fee(amount):
        amount = Decimal(str(amount))
        fee = amount * FEE_PCT + (FEE_FIXED_EUR if amount > Decimal("0") else Decimal("0"))
        return fee.quantize(Decimal("0.01"))

"""

#universal cureency
from decimal import Decimal
from django import forms
from .constants import MIN_EUR, MAX_EUR, FEE_PCT, FEE_FIXED_EUR
from .models import Currency  # your TextChoices: EUR, USD, GBP

class WithdrawalForm(forms.Form):
    amount = forms.DecimalField(min_value=0, decimal_places=2, max_digits=12)

    # Currency comes from user's selection (EUR/USD/GBP); defaults to EUR in the view if empty
    currency = forms.ChoiceField(choices=(), required=False)

    address_id = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount") or Decimal("0")
        if amount < MIN_EUR or amount > MAX_EUR:
            raise forms.ValidationError(
                f"Amount must be between €{MIN_EUR} and €{MAX_EUR}."
            )
        return cleaned

    @staticmethod
    def compute_fee(amount):
        amount = Decimal(str(amount))
        fee = amount * FEE_PCT + (FEE_FIXED_EUR if amount > Decimal("0") else Decimal("0"))
        return fee.quantize(Decimal("0.01"))



# --- Deposit ---
PRESETS = [10,20,30,50,100,300,500,1000]
CURRENCY_SYMBOL = {"EUR":"€", "USD":"$", "GBP":"£"}

class DepositForm(forms.Form):
    amount = forms.DecimalField(min_value=0, decimal_places=2, max_digits=12)
    currency = forms.ChoiceField(choices=Currency.choices, initial=Currency.EUR)
    network = forms.ChoiceField(choices=Network.choices)

    def clean_amount(self):
        amt = self.cleaned_data["amount"]
        if amt < 10:
            raise forms.ValidationError("Minimum amount is 10.")
        return amt



# Reuse your COUNTRY_CODES / _DIAL_RULES
COUNTRY_CODE_CHOICES = [(dial, disp) for dial, disp, _, _ in COUNTRY_CODES]

class ChangePasswordForm(PasswordChangeForm):
    """
    Dynamic change-password:
    - country_code + phone_number (normalized to E.164)
    - captcha (django-simple-captcha)
    - old_password / new_password1 / new_password2 (PasswordChangeForm)
    """
    country_code = forms.ChoiceField(
        choices=COUNTRY_CODE_CHOICES,
        widget=forms.Select(attrs={'class': 'country-select'}),
        label=_("Country code"),
    )
    phone_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={
            'placeholder': _("Enter your phone number"),
            'autocomplete': 'tel',
            'class': 'cc-input',
        }),
        label=_("Phone number"),
    )
    captcha = CaptchaField(label=_("I am not a robot"))

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)   # parity with your SignupForm
        super().__init__(*args, **kwargs)

        # Prefill from user.phone if present
        stored = getattr(self.user, "phone", None) or getattr(self.user, "phone_number", None)
        if stored and isinstance(stored, str) and stored.startswith("+"):
            try:
                pn = phonenumbers.parse(stored, None)
                cc = f"+{pn.country_code}"
                national = re.sub(r"\D+", "", str(pn.national_number))
                if cc in dict(COUNTRY_CODE_CHOICES):
                    self.fields["country_code"].initial = cc
                self.fields["phone_number"].initial = national
            except Exception:
                pass

        # UX hints for password fields
        for name in ("old_password", "new_password1", "new_password2"):
            if name in self.fields:
                self.fields[name].widget.attrs.update({
                    "placeholder": _("Enter your password"),
                    "class": "pwd-input",
                    "autocomplete": "current-password" if name == "old_password" else "new-password",
                })

    def _normalize_to_e164(self, country_dial: str, raw: str) -> str:
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

    def clean(self):
        cleaned = super().clean()
        dial = cleaned.get("country_code")
        raw = cleaned.get("phone_number")

        if dial and raw:
            # Optional length hint like your Login/Signup
            disp, lo, hi = _DIAL_RULES.get(dial, (None, None, None))
            local_digits = ''.join(ch for ch in raw if ch.isdigit())
            if lo and hi and not (lo <= len(local_digits) <= hi):
                self.add_error('phone_number', _(
                    "Phone number length must be between %(lo)d and %(hi)d digits for %(country)s."
                ) % {"lo": lo, "hi": hi, "country": disp})
                return cleaned

            try:
                normalized = self._normalize_to_e164(dial, raw)
            except forms.ValidationError as e:
                self.add_error('phone_number', e)
                return cleaned

            cleaned["phone"] = normalized
            user_phone = getattr(self.user, "phone", None) or getattr(self.user, "phone_number", None)
            if user_phone and str(user_phone) != normalized:
                self.add_error('phone_number', _("Phone does not match your account."))

        return cleaned
