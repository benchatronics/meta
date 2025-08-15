# main/forms.py
from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm, ReadOnlyPasswordHashField
from django.utils.translation import gettext_lazy as _
from captcha.fields import CaptchaField
import phonenumbers
import requests
from decimal import Decimal
from .models import PayoutAddress, AddressType, Currency, Network
from .constants import MIN_EUR, MAX_EUR, FEE_PCT, FEE_FIXED_EUR
from .models import CustomUser
from .country_codes import COUNTRY_CODES  # (dial, display, min_len, max_len)





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
    invitation_code = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': _("Enter your invitation code"),
            'class': 'text-input'
        }),
        label=_("Invitation code"),
    )
    captcha = CaptchaField(label=_("I am not a robot"))

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

    def clean(self):
        cleaned_data = super().clean()
        dial = cleaned_data.get("country_code")
        raw = cleaned_data.get("phone_number")

        if not dial or not raw:
            return cleaned_data

        disp, lo, hi = _DIAL_RULES.get(dial, (None, None, None))
        local_digits = ''.join(ch for ch in raw if ch.isdigit())
        if lo and hi and not (lo <= len(local_digits) <= hi):
            self.add_error('phone_number', _("Phone number length must be between %(lo)d and %(hi)d digits for %(country)s.") % {"lo": lo, "hi": hi, "country": disp})
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

    def save(self, commit=True):
        user = super().save(commit=False)
        user.phone = self.cleaned_data.get('phone') or f"{self.cleaned_data['country_code']}{''.join(ch for ch in self.cleaned_data['phone_number'] if ch.isdigit())}"
        user.username = user.phone
        user.invitation_code = self.cleaned_data.get('invitation_code', '')

        # Capture signup IP & country (free API)
        ip = _client_ip_from_request(self.request)
        if ip:
            user.signup_ip = ip
            user.signup_country = _country_from_ip(ip)

        if commit:
            user.save()
        return user


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

class AddressForm(forms.ModelForm):
    class Meta:
        model = PayoutAddress
        fields = ["address_type", "address", "label"]

    def clean_address(self):
        addr_type = self.cleaned_data.get("address_type")
        addr = (self.cleaned_data.get("address") or "").strip()
        if addr_type == AddressType.ETH:
            if not (addr.startswith("0x") and len(addr) == 42):
                raise forms.ValidationError("Invalid Ethereum address.")
        elif addr_type == AddressType.TRC20:
            if not (addr.startswith("T") and 30 <= len(addr) <= 50):
                raise forms.ValidationError("Invalid TRC20 address.")
        return addr
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


class AddressForm(forms.ModelForm):
    class Meta:
        model = PayoutAddress
        fields = ["address_type", "address", "label"]
        widgets = {
            "address": forms.TextInput(attrs={"class": "pill-input"}),
            "label": forms.TextInput(attrs={"class": "pill-input"}),
        }
