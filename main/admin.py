
from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django.http import HttpResponse
import csv
from django import forms
from typing import Optional
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from .forms import AdminUserCreationForm, AdminUserChangeForm
from .services import confirm_deposit
from django.db import transaction
from django.utils.html import format_html
from .models import CustomUser
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from .models import Wallet
from .task import account_snapshot, detect_phase
from .forms import AdminUserCreationForm, AdminUserChangeForm  # your existing forms
from .models import (
    CustomUser,
    InfoPage,
    Announcement,
    Country,
    Hotel,
    Favorite,
    unique_slugify,
    SystemSettings,
    Task,
    Wallet,
    PayoutAddress,
    WithdrawalRequest,
    DepositAddress,
    DepositRequest,
)



#task
@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = ("trial_task_cost_cents", "trial_commission_cents", "trial_max_tasks",
                    "normal_task_limit", "normal_commission_cents", "normal_worth_cents",
                    "vip_default_commission_cents", "updated_at")
    readonly_fields = ("updated_at",)

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("user", "phase", "index_in_phase", "status",
                    "cost_cents", "commission_cents", "worth_cents",
                    "deposit_required_cents", "created_at")
    list_filter = ("phase", "status")
    search_fields = ("user__phone", "user__nickname", "idempotency_key")




# ------------------ CUSTOM USER ADMIN ------------------

# Safe i18n import across Django versions (defines `_`)
try:
    from django.utils.translation import gettext_lazy as _
except Exception:  # Django < 3.0
    from django.utils.translation import ugettext_lazy as _

# ----- Change form: clear or set withdrawal PIN -----
class CustomUserChangeForm(forms.ModelForm):
    clear_tx_pin = forms.BooleanField(
        label=_("Clear withdrawal PIN"),
        required=False,
        help_text=_("Tick to remove any existing withdrawal PIN so the user must set a new one.")
    )
    new_tx_pin = forms.CharField(
        label=_("Set new withdrawal PIN"),
        required=False,
        widget=forms.PasswordInput(attrs={
            "autocomplete": "new-password",
            "inputmode": "numeric",
            "pattern": r"\d{6}",
            "placeholder": "••••••",
        }),
        help_text=_("Optional. Enter exactly 6 digits to set/reset the PIN.")
    )

    class Meta:
        model = CustomUser
        fields = (
            "phone", "nickname", "avatar", "avatar_url",
            "is_active", "is_staff", "is_superuser", "groups", "user_permissions",
        )

    def clean_new_tx_pin(self) -> Optional[str]:
        pin = (self.cleaned_data.get("new_tx_pin") or "").strip()
        if pin and (not pin.isdigit() or len(pin) != 6):
            raise forms.ValidationError(_("PIN must be exactly 6 digits."))
        return pin or None

    def save(self, commit: bool = True):
        user = super().save(commit=False)

        # Clear first if requested
        if self.cleaned_data.get("clear_tx_pin"):
            if hasattr(user, "clear_tx_pin") and callable(user.clear_tx_pin):
                user.clear_tx_pin()
            else:
                user.tx_pin_hash = ""

        # Set new PIN if provided
        pin = self.cleaned_data.get("new_tx_pin")
        if pin:
            if hasattr(user, "set_tx_pin") and callable(user.set_tx_pin):
                user.set_tx_pin(pin)
            else:
                from django.contrib.auth.hashers import make_password
                user.tx_pin_hash = make_password(pin)

        if commit:
            user.save()
            self.save_m2m()
        return user


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    # If you don't use AdminUserCreationForm, remove this line or replace with your own.
    add_form = AdminUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser

    @admin.display(description="Avatar")
    def avatar_preview(self, obj):
        url = getattr(obj, "display_avatar", None)
        if not url:
            return "—"
        return format_html(
            '<img src="{}" style="width:32px;height:32px;border-radius:50%;'
            'object-fit:cover;display:block;" alt="avatar" />',
            url,
        )

    list_display = (
        "id", "avatar_preview", "phone", "nickname",
        "signup_ip", "signup_country", "last_login_ip", "last_login_country",
        "invitation_code", "is_active", "is_staff", "date_joined",
    )
    list_display_links = ("id", "phone")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = (
        "phone", "nickname", "invitation_code",
        "signup_ip", "signup_country", "last_login_ip", "last_login_country",
    )
    ordering = ("-date_joined",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("nickname", "avatar", "avatar_url", "avatar_preview")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
        ("Extras", {"fields": ("invitation_code", "signup_ip", "signup_country", "last_login_ip", "last_login_country")}),
        # NEW admin controls
        (_("Withdrawal security"), {"fields": ("clear_tx_pin", "new_tx_pin")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("phone", "password1", "password2", "is_active", "is_staff", "is_superuser", "groups"),
        }),
    )

    readonly_fields = (
        "date_joined", "last_login",
        "signup_ip", "signup_country", "last_login_ip", "last_login_country",
        "avatar_preview",
    )

# ------------------ COUNTRY ADMIN ------------------
@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("name", "iso", "flag")
    search_fields = ("name", "iso")
    ordering = ("name",)


# ------------------ HOTEL ADMIN ------------------
class HotelAdminForm(forms.ModelForm):
    class Meta:
        model = Hotel
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Show slug but prevent manual edits
        if "slug" in self.fields:
            self.fields["slug"].disabled = True
            self.fields["slug"].required = False


@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    form = HotelAdminForm

    list_display = (
        "name",
        "country",
        "city",
        "score",
        "label",
        "is_recommended",
        "popularity",
        "is_published",
        "created_at",
        "favorites_count",
    )
    list_filter = ("label", "is_recommended", "is_published", "country")
    search_fields = ("name", "city", "description_short", "slug")
    readonly_fields = ("created_at", "favorites_count")  # slug handled in form
    ordering = ("-created_at",)
    autocomplete_fields = ("country",)

    # keep prepopulated UI so you see the slug preview, though the field is disabled
    prepopulated_fields = {"slug": ("name",)}

    def save_model(self, request, obj, form, change):
        """
        Auto-generate/refresh slug:
        - On create: always generate.
        - On update: regenerate only if the name changed.
        Uses unique_slugify to avoid collisions.
        """
        if not change:
            # creating
            obj.slug = unique_slugify(obj, obj.name)
        else:
            # updating
            if "name" in form.changed_data:
                obj.slug = unique_slugify(obj, obj.name)
        super().save_model(request, obj, form, change)


# ------------------ FAVORITE ADMIN ------------------
@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "hotel", "created_at")
    search_fields = ("user__phone", "hotel__name")
    list_filter = ("created_at",)
    autocomplete_fields = ("user", "hotel")


# ------------------ WALLET / PAYOUT / WITHDRAWAL / DEPOSIT ADDRESS ADMINS ------------------

# --- Optional inline for ledger (WalletTxn). Safe if model not present. ---
try:
    from .models import WalletTxn

    class WalletTxnInline(admin.TabularInline):
        model = WalletTxn
        extra = 0
        can_delete = False
        ordering = ("-created_at",)
        # Show bucket + friendly € column; read-only inline
        readonly_fields = ("created_at", "kind", "bucket", "amount_eur_safe", "memo", "created_by")
        fields = ("created_at", "kind", "bucket", "amount_eur_safe", "memo", "created_by")

        @admin.display(description="Amount (€)")
        def amount_eur_safe(self, obj):
            # Defensive: handle None or bad values gracefully
            try:
                cents = int(obj.amount_cents) if obj.amount_cents is not None else 0
            except (TypeError, ValueError):
                cents = 0
            sign = "-" if cents < 0 else ""
            return f"{sign}€{abs(cents) / 100:,.2f}"

except Exception:
    WalletTxnInline = None  # inline hidden if model not available


# --- Filter: has/hasn't received signup bonus ---
class HasTrialBonusFilter(admin.SimpleListFilter):
    title = "Has trial bonus?"
    parameter_name = "has_bonus"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(trial_bonus_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(trial_bonus_at__isnull=True)
        return queryset


# --- Actions ---
@admin.action(description="Grant €300 trial bonus to selected (only if not already granted)")
def grant_trial_bonus(modeladmin, request, queryset):
    bonus_eur = int(getattr(settings, "TRIAL_BONUS_EUR", 300))
    if not getattr(settings, "TRIAL_BONUS_ENABLED", True) or bonus_eur <= 0:
        messages.error(request, "Trial bonus is disabled in settings.")
        return

    bonus_cents = bonus_eur * 100
    # Only wallets that haven't been granted
    qs = queryset.filter(trial_bonus_at__isnull=True)

    granted = 0
    for w in qs.select_for_update():
        with transaction.atomic():
            # Double-check inside txn
            updated = Wallet.objects.filter(pk=w.pk, trial_bonus_at__isnull=True).update(
                bonus_cents=F("bonus_cents") + bonus_cents,
                trial_bonus_at=timezone.now(),
            )
            if not updated:
                continue
            granted += 1

            # Write a ledger row if the model exists
            try:
                WalletTxn.objects.create(
                    wallet=w,
                    amount_cents=bonus_cents,
                    kind="BONUS",
                    bucket="BONUS",
                    memo="Admin action: signup trial bonus",
                    created_by=getattr(request, "user", None),
                )
            except Exception:
                pass

    if granted:
        messages.success(request, f"Granted €{bonus_eur} trial bonus to {granted} wallet(s).")
    else:
        messages.info(request, "No wallets updated (maybe all selected already have the bonus).")


@admin.action(description="Export selected wallets to CSV")
def export_wallets_csv(modeladmin, request, queryset):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="wallets.csv"'
    writer = csv.writer(resp)
    writer.writerow([
        "user_id", "username", "email", "phone",
        "cash_eur", "bonus_eur", "total_eur", "pending_eur",
        "trial_bonus_at",
    ])
    for w in queryset.select_related("user"):
        u = w.user
        writer.writerow([
            getattr(u, "id", ""),
            getattr(u, "username", ""),
            getattr(u, "email", ""),
            getattr(u, "phone", ""),  # remove if your User has no phone field
            f"{w.balance_cents / 100:.2f}",
            f"{w.bonus_cents / 100:.2f}",
            f"{(w.balance_cents + w.bonus_cents) / 100:.2f}",
            f"{w.pending_cents / 100:.2f}",
            w.trial_bonus_at.isoformat() if w.trial_bonus_at else "",
        ])
    return resp



# Optional: AccountDisplay support (AUTO/MANUAL). Falls back gracefully if missing.
try:
    from .models import AccountDisplay, AccountDisplayMode
except Exception:
    AccountDisplay = None
    AccountDisplayMode = None


# ---- Optional filter by display mode (works only if AccountDisplay exists) ----
class DisplayModeFilter(SimpleListFilter):
    title = "Display mode"
    parameter_name = "display_mode"

    def lookups(self, request, model_admin):
        if AccountDisplayMode is None:
            return ()
        return [
            (AccountDisplayMode.AUTO, "Auto (computed)"),
            (AccountDisplayMode.MANUAL, "Manual (override)"),
        ]

    def queryset(self, request, qs):
        if AccountDisplay is None or AccountDisplayMode is None:
            return qs
        # Support either AccountDisplay linked to user or wallet
        # Try wallet relation first
        if hasattr(AccountDisplay, "wallet_id"):
            return qs.filter(display__mode=self.value()) if self.value() else qs
        # Else user relation
        return qs.filter(user__account_display__mode=self.value()) if self.value() else qs


# ---- Wallet admin with snapshot columns ----
@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "cash_eur_col",
        "bonus_eur_col",
        "total_eur_col",
        "pending_eur_col",
        "phase_col",                # NEW
        "display_mode_col",         # NEW (AUTO/MANUAL)
        "total_assets_col",         # NEW
        "asset_col",                # NEW
        "dividends_col",            # NEW
        "processing_col",           # NEW
        "trial_bonus_at",
    )
    list_display_links = ("user",)
    list_filter = (DisplayModeFilter,)  # keep your other filters if any (e.g., HasTrialBonusFilter)
    search_fields = ("user__username", "user__email", "user__phone", "user__id")
    autocomplete_fields = ("user",)
    ordering = ("-balance_cents",)
    list_select_related = ("user",)  # (don't add 'display' blindly; may not exist)
    readonly_fields = (
        "trial_bonus_at",
        # show snapshot also on change page
        "phase_col",
        "display_mode_col",
        "total_assets_col",
        "asset_col",
        "dividends_col",
        "processing_col",
    )
    # Keep your inlines/actions if you use them; will no-op if undefined
    inlines = []
    actions = ["set_display_manual", "set_display_auto"]
    empty_value_display = "—"

    # -------- Money columns you already had --------
    @admin.display(description="Cash (€)", ordering="balance_cents")
    def cash_eur_col(self, obj):
        return f"€{(obj.balance_cents or 0) / 100:,.2f}"

    @admin.display(description="Bonus (€)", ordering="bonus_cents")
    def bonus_eur_col(self, obj):
        cents = getattr(obj, "bonus_cents", 0) or 0
        return f"€{cents / 100:,.2f}"

    @admin.display(description="Total (€)")
    def total_eur_col(self, obj):
        cents = (obj.balance_cents or 0) + (getattr(obj, "bonus_cents", 0) or 0)
        return f"€{cents / 100:,.2f}"

    @admin.display(description="Pending (€)", ordering="pending_cents")
    def pending_eur_col(self, obj):
        return f"€{(obj.pending_cents or 0) / 100:,.2f}"

    @admin.display(boolean=True, description="Has bonus?")
    def has_bonus(self, obj):
        return obj.trial_bonus_at is not None

    # -------- Snapshot helpers (cached per row) --------
    def _snap(self, obj):
        # cache to avoid recomputing 5x per row
        if not hasattr(obj, "_snapshot_cache"):
            obj._snapshot_cache = account_snapshot(obj.user)
        return obj._snapshot_cache

    @admin.display(description="Phase")
    def phase_col(self, obj):
        try:
            return detect_phase(obj.user)
        except Exception:
            return "—"

    @admin.display(description="Display Mode")
    def display_mode_col(self, obj):
        if AccountDisplay is None or AccountDisplayMode is None:
            return "AUTO"
        # support either wallet-linked or user-linked AccountDisplay
        mode = None
        try:
            if hasattr(obj, "display"):
                mode = obj.display.mode
            elif hasattr(obj.user, "account_display"):
                mode = obj.user.account_display.mode
        except Exception:
            mode = None
        return mode or "AUTO"

    @admin.display(description="Total Assets")
    def total_assets_col(self, obj):
        return self._snap(obj)["total_assets_eur"]

    @admin.display(description="Asset")
    def asset_col(self, obj):
        return self._snap(obj)["asset_eur"]

    @admin.display(description="Dividends")
    def dividends_col(self, obj):
        return self._snap(obj)["dividends_eur"]

    @admin.display(description="Processing")
    def processing_col(self, obj):
        return self._snap(obj)["processing_eur"]

    # -------- Bulk actions: set MANUAL / AUTO (if AccountDisplay exists) --------
    def _get_or_create_display(self, wallet):
        if AccountDisplay is None:
            return None
        # If AccountDisplay is OneToOne with wallet (recommended)
        if hasattr(AccountDisplay, "wallet_id"):
            disp, _ = AccountDisplay.objects.get_or_create(wallet=wallet)
            return disp
        # Else OneToOne with user (older variant)
        disp, _ = AccountDisplay.objects.get_or_create(user=wallet.user)
        return disp

    @admin.action(description="Set Display Mode → MANUAL (keep current numbers)")
    def set_display_manual(self, request, queryset):
        if AccountDisplay is None or AccountDisplayMode is None:
            self.message_user(request, "AccountDisplay model not installed.", level=messages.ERROR)
            return
        updated = 0
        for wallet in queryset:
            disp = self._get_or_create_display(wallet)
            if not disp:
                continue
            # capture current computed snapshot into stored cents
            snap = account_snapshot(wallet.user)
            disp.total_assets_cents = snap["total_assets_cents"]
            disp.asset_cents        = snap["asset_cents"]
            disp.dividends_cents    = snap["dividends_cents"]
            disp.processing_cents   = snap["processing_cents"]
            disp.mode = AccountDisplayMode.MANUAL
            disp.save(update_fields=[
                "total_assets_cents", "asset_cents", "dividends_cents", "processing_cents", "mode", "updated_at"
            ])
            updated += 1
        self.message_user(request, f"Switched {updated} wallet(s) to MANUAL.", level=messages.SUCCESS)

    @admin.action(description="Set Display Mode → AUTO")
    def set_display_auto(self, request, queryset):
        if AccountDisplay is None or AccountDisplayMode is None:
            self.message_user(request, "AccountDisplay model not installed.", level=messages.ERROR)
            return
        updated = 0
        for wallet in queryset:
            disp = self._get_or_create_display(wallet)
            if not disp:
                continue
            disp.mode = AccountDisplayMode.AUTO
            disp.save(update_fields=["mode", "updated_at"])
            updated += 1
        self.message_user(request, f"Switched {updated} wallet(s) to AUTO.", level=messages.SUCCESS)


@admin.register(PayoutAddress)
class PayoutAddressAdmin(admin.ModelAdmin):
    list_display = ("user", "address_type", "address", "is_verified", "created_at")
    list_filter = ("address_type", "is_verified")
    search_fields = ("user__phone", "address")
    autocomplete_fields = ("user",)

"""
@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "amount_cents", "currency", "address", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("user__phone", "address")
    autocomplete_fields = ("user",)
"""

@admin.action(description="Confirm selected withdrawals (complete & reduce pending)")
def mark_withdrawals_completed(modeladmin, request, queryset):
    with transaction.atomic():
        done = 0
        for w in queryset.select_for_update():
            if hasattr(w, "confirm") and w.confirm():
                done += 1
    messages.success(request, f"Completed {done} withdrawal(s).")

@admin.action(description="Fail selected withdrawals (refund user)")
def mark_withdrawals_failed(modeladmin, request, queryset):
    with transaction.atomic():
        done = 0
        for w in queryset.select_for_update():
            if hasattr(w, "fail") and w.fail(reason="Marked failed in admin"):
                done += 1
    messages.warning(request, f"Failed {done} withdrawal(s) and refunded users.")

@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "amount_display", "currency",
        "status_badge", "address", "created_at",
        "processed_at_display",  # safe callable (no E108)
        "txid_short",
    )
    list_filter = ("status", "currency", "created_at")
    search_fields = ("id", "user__phone", "user__email", "txid", "address__address")
    actions = [mark_withdrawals_completed, mark_withdrawals_failed]

    # Use callables in readonly_fields to avoid E035 if the model lacks the field
    readonly_fields = ("created_at", "processed_at_display")

    # ----- Display helpers -----
    def amount_display(self, obj):
        try:
            return f"{obj.amount:.2f}"
        except Exception:
            return f"{obj.amount_cents/100:.2f}"
    amount_display.short_description = "Amount"

    def status_badge(self, obj):
        colors = {
            "pending":    "#f59e0b",
            "processing": "#3b82f6",
            "completed":  "#10b981",
            "failed":     "#ef4444",
            # legacy safety
            "success":    "#10b981",
        }
        label = getattr(obj, "get_status_display", lambda: str(obj.status).title())()
        c = colors.get(obj.status, "#6b7280")
        return format_html(
            '<span style="padding:2px 8px;border-radius:9999px;background:{}20;color:{};font-weight:600;">{}</span>',
            c, c, label
        )
    status_badge.short_description = "Status"

    def txid_short(self, obj):
        txid = getattr(obj, "txid", "") or ""
        return (txid[:10] + "…") if txid else ""
    txid_short.short_description = "Tx"

    def processed_at_display(self, obj):
        # Works even if the model doesn’t have processed_at yet
        return getattr(obj, "processed_at", "") or ""
    processed_at_display.short_description = "Processed at"


@admin.register(DepositAddress)
class DepositAddressAdmin(admin.ModelAdmin):
    list_display = ("network", "address", "active", "updated_at")
    list_filter = ("network", "active")
    search_fields = ("address",)


# ------------------ DEPOSIT REQUEST ADMIN (DEDUPED) ------------------
@admin.action(description="Confirm & credit selected deposits")
def admin_confirm_deposits(modeladmin, request, queryset):
    count = 0
    for dep in queryset:
        if confirm_deposit(dep):
            count += 1
    if count:
        messages.success(request, f"Confirmed {count} deposit(s).")
    else:
        messages.info(request, "No deposits were confirmed (already confirmed or invalid state).")


@admin.register(DepositRequest)
class DepositRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "reference", "amount_cents", "currency", "network", "status", "created_at")
    list_filter = ("status", "currency", "network")
    search_fields = ("reference", "user__phone")
    autocomplete_fields = ("user",)
    actions = [admin_confirm_deposits]


@admin.register(InfoPage)
class InfoPageAdmin(admin.ModelAdmin):
    list_display = ("key", "title", "is_published", "updated_at")
    list_filter = ("is_published", "key")
    search_fields = ("title", "body")

@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "pinned", "is_published", "starts_at", "ends_at", "created_at")
    list_filter = ("pinned", "is_published")
    search_fields = ("title", "body")


from .models import TaskTemplate
@admin.register(TaskTemplate)
class TaskTemplateAdmin(admin.ModelAdmin):
    list_display  = ("name", "phase", "order_code", "country", "city",
                     "worth_cents", "commission_cents", "score", "label",
                     "is_published", "is_recommended", "popularity", "created_at")
    list_filter   = ("phase", "is_published", "label")
    search_fields = ("name", "slug", "order_code", "country", "city")
    readonly_fields = ("created_at",)




"""

from .models import AccountDisplay, AccountDisplayMode

@admin.register(AccountDisplay)
class AccountDisplayAdmin(admin.ModelAdmin):
    list_display = (
        "user", "mode",
        "total_assets_eur", "asset_eur", "dividends_eur", "processing_eur",
        "updated_at",
    )
    list_filter = ("mode",)
    search_fields = ("user__phone", "user__nickname")
    # Allow editing cents directly on the change page
    fields = (
        "user", "mode",
        ("total_assets_cents", "asset_cents"),
        ("dividends_cents", "processing_cents"),
        "updated_at",
    )
    readonly_fields = ("updated_at",)

"""

# admin.py (append)
from decimal import Decimal, ROUND_HALF_UP
from django import forms
from django.contrib import admin
from .models import AccountDisplay, AccountDisplayMode

def _eur_to_cents(value: Decimal | None) -> int:
    if value is None:
        return 0
    return int((value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def _cents_to_eur(cents: int | None) -> Decimal:
    cents = cents or 0
    return (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

class AccountDisplayForm(forms.ModelForm):
    # Editable Euro fields for admin convenience
    total_assets_eur = forms.DecimalField(label="Total Assets (€)", max_digits=18, decimal_places=2, required=False)
    asset_eur        = forms.DecimalField(label="Asset (€)",        max_digits=18, decimal_places=2, required=False)
    dividends_eur    = forms.DecimalField(label="Dividends (€)",    max_digits=18, decimal_places=2, required=False)
    processing_eur   = forms.DecimalField(label="Processing (€)",   max_digits=18, decimal_places=2, required=False)

    class Meta:
        model = AccountDisplay
        fields = ("user", "mode", "total_assets_eur", "asset_eur", "dividends_eur", "processing_eur")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Initialize Euro fields from stored cents
        if self.instance and self.instance.pk:
            self.fields["total_assets_eur"].initial = _cents_to_eur(self.instance.total_assets_cents)
            self.fields["asset_eur"].initial        = _cents_to_eur(self.instance.asset_cents)
            self.fields["dividends_eur"].initial    = _cents_to_eur(self.instance.dividends_cents)
            self.fields["processing_eur"].initial   = _cents_to_eur(self.instance.processing_cents)

    def save(self, commit=True):
        obj = super().save(commit=False)
        # Persist cents from Euro inputs
        obj.total_assets_cents = _eur_to_cents(self.cleaned_data.get("total_assets_eur"))
        obj.asset_cents        = _eur_to_cents(self.cleaned_data.get("asset_eur"))
        obj.dividends_cents    = _eur_to_cents(self.cleaned_data.get("dividends_eur"))
        obj.processing_cents   = _eur_to_cents(self.cleaned_data.get("processing_eur"))
        if commit:
            obj.save()
        return obj

@admin.register(AccountDisplay)
class AccountDisplayAdmin(admin.ModelAdmin):
    form = AccountDisplayForm
    list_display = ("user", "mode", "total_assets_display", "asset_display", "dividends_display", "processing_display", "updated_at")
    list_filter  = ("mode",)
    search_fields = ("user__phone", "user__nickname", "user__username", "user__email")
    readonly_fields = ("updated_at",)

    fields = (
        "user", "mode",
        ("total_assets_eur", "asset_eur"),
        ("dividends_eur", "processing_eur"),
        "updated_at",
    )

    @admin.display(description="Total Assets (€)")
    def total_assets_display(self, obj): return obj.total_assets_eur

    @admin.display(description="Asset (€)")
    def asset_display(self, obj): return obj.asset_eur

    @admin.display(description="Dividends (€)")
    def dividends_display(self, obj): return obj.dividends_eur

    @admin.display(description="Processing (€)")
    def processing_display(self, obj): return obj.processing_eur
