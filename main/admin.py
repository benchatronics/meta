# main/admin.py
from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin

from .forms import AdminUserCreationForm, AdminUserChangeForm
from .models import (
    CustomUser,
    Country,
    Hotel,
    Favorite,
    unique_slugify,
    Wallet,
    PayoutAddress,
    WithdrawalRequest,
    DepositAddress,
    DepositRequest,
)
from .services import confirm_deposit

from django.db import transaction
from django.utils.html import format_html


# ------------------ CUSTOM USER ADMIN ------------------
@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = AdminUserCreationForm
    form = AdminUserChangeForm
    model = CustomUser

    list_display = (
        "id",
        "phone",
        "signup_ip",
        "signup_country",
        "last_login_ip",
        "last_login_country",
        "invitation_code",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = (
        "phone",
        "invitation_code",
        "signup_ip",
        "signup_country",
        "last_login_ip",
        "last_login_country",
    )
    ordering = ("-date_joined",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
        ("Extras", {
            "fields": (
                "invitation_code",
                "signup_ip",
                "signup_country",
                "last_login_ip",
                "last_login_country",
            )
        }),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("phone", "password1", "password2", "is_active", "is_staff", "is_superuser", "groups"),
        }),
    )

    readonly_fields = ("date_joined", "last_login", "signup_ip", "signup_country", "last_login_ip", "last_login_country")


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
@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance_cents", "pending_cents")
    search_fields = ("user__phone",)
    autocomplete_fields = ("user",)


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
