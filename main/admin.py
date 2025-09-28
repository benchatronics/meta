from __future__ import annotations
import csv
from django.contrib import messages
from django.shortcuts import redirect
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from django.middleware.csrf import get_token

from django import forms
from django.apps import apps
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from .models import CustomUser
#from .models import InvitationCode, InvitationUsage

# ---- Models you actually have (per your models.py) ----
from .models import (
    CustomUser,
    Wallet, WalletTxn,
    Country, Hotel, Favorite,
    PayoutAddress, WithdrawalRequest,
    DepositAddress, DepositRequest,
    InfoPage, Announcement,
    tasksettngs as TaskSettings,  # singleton
    UserTaskTemplate,
)


#Change admin names
admin.site.site_header = "Orbitpedia Administration"
admin.site.site_title = "Orbitpedia Admin"
admin.site.index_title = "Welcome to Orbitpedia Administration"

from .models import InvitationLink

@admin.register(InvitationLink)
class InvitationLinkAdmin(admin.ModelAdmin):
    list_display = ("code", "owner", "is_active", "expires_at", "claimed", "used_by", "used_at", "created_at")
    list_filter = ("is_active", "claimed", "expires_at", "created_at")
    search_fields = ("code", "owner__username", "owner__email", "label")
    actions = ["activate_links", "suspend_links"]

    def activate_links(self, request, queryset):
        queryset.update(is_active=True)
    activate_links.short_description = "Activate selected invitations"

    def suspend_links(self, request, queryset):
        queryset.update(is_active=False)
    suspend_links.short_description = "Suspend selected invitations"

    def save_model(self, request, obj, form, change):
        if not obj.code:
            obj.code = InvitationLink.generate_code()
        obj.code = obj.code.upper()
        super().save_model(request, obj, form, change)


# in admin.py
#from django.core.exceptions import ValidationError

@admin.action(description="Approve SUBMITTED admin tasks (debit price, credit payout, update dashboard)")
def approve_admin_submitted(self, request, queryset):
    ok = skipped = failed = 0
    for ut in queryset.select_related("template", "user"):
        try:
            # must be an ADMIN task that is SUBMITTED
            if ut.task_kind != UserTask.Kind.ADMIN or ut.status != UserTask.Status.SUBMITTED:
                skipped += 1
                continue

            ut.approve_admin(approved_by=getattr(request, "user", None))
            ok += 1

        except ValidationError as e:
            failed += 1
            self.message_user(request, f"Task #{ut.pk}: {e}", level=messages.ERROR)
        except Exception as e:
            failed += 1
            self.message_user(request, f"Task #{ut.pk}: {e}", level=messages.ERROR)

    if ok:
        self.message_user(request, f"Approved {ok} admin task(s).", level=messages.SUCCESS)
    if skipped:
        self.message_user(request, f"Skipped {skipped} non-admin or non-submitted task(s).", level=messages.INFO)
    if failed:
        self.message_user(request, f"Failed {failed} task(s). See errors above.", level=messages.ERROR)



# Pull these via apps.get_model to avoid import-order surprises
APP_LABEL = "main"
UserTask = apps.get_model(APP_LABEL, "UserTask")
UserTaskProgress = apps.get_model(APP_LABEL, "UserTaskProgress")
ForcedTaskDirective = apps.get_model(APP_LABEL, "ForcedTaskDirective")


# ======================
# Utility (EUR <-> cents)
# ======================
def _eur_to_cents(value: Decimal | None) -> int:
    if value is None:
        return 0
    return int((value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def _cents_to_eur(cents: int | None) -> Decimal:
    cents = cents or 0
    return (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ===========================
# ForcedTaskDirective Admin
# ===========================
@admin.register(ForcedTaskDirective)
class ForcedTaskDirectiveAdmin(admin.ModelAdmin):
    list_display = (
        "user", "applies_on_cycle", "target_order", "template",
        "status", "expires_at", "reason", "batch_id",
        "created_by", "created_at",
    )
    list_filter = ("status", "applies_on_cycle", "expires_at", "created_at")
    search_fields = (
        "user__username", "user__email", "user__phone", "user__nickname",
        "reason", "batch_id",
        "template__hotel_name", "template__slug", "template__task_id",
    )
    autocomplete_fields = ("user", "template", "created_by")
    ordering = ("applies_on_cycle", "target_order", "-created_at")
    list_per_page = 50
    readonly_fields = ("created_at", "updated_at", "consumed_at", "canceled_at", "expired_at", "skipped_at")

    actions = ["mark_pending", "cancel_selected", "expire_selected_now"]

    @admin.action(description="Mark selected as PENDING (re-enable)")
    def mark_pending(self, request, queryset):
        now = timezone.now()
        updated = queryset.update(status="PENDING", updated_at=now)
        self.message_user(request, f"Re-enabled {updated} directive(s).", level=messages.SUCCESS)

    @admin.action(description="Cancel selected directives")
    def cancel_selected(self, request, queryset):
        now = timezone.now()
        updated = queryset.exclude(status="CANCELED").update(status="CANCELED", canceled_at=now, updated_at=now)
        self.message_user(request, f"Canceled {updated} directive(s).", level=messages.SUCCESS)

    @admin.action(description="Expire selected directives (now)")
    def expire_selected_now(self, request, queryset):
        now = timezone.now()
        updated = queryset.exclude(status="EXPIRED").update(status="EXPIRED", expired_at=now, updated_at=now)
        self.message_user(request, f"Expired {updated} directive(s).", level=messages.SUCCESS)


# ======================
# UserTask Admin
# ======================
@admin.register(UserTask)
class UserTaskAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "template",
        "task_kind", "cycle_number", "order_shown",
        "status", "price_used", "commission_used",
        "created_at",
    )
    list_filter = ("status", "task_kind", "cycle_number", "created_at")
    search_fields = (
        "user__username", "user__email", "user__phone", "user__nickname",
        "template__hotel_name", "template__slug", "template__task_id",
    )
    autocomplete_fields = ("user", "template")
    readonly_fields = ("created_at", "started_at", "submitted_at", "decided_at")
    ordering = ("-created_at",)
    list_per_page = 50

    fieldsets = (
        ("Links", {"fields": ("user", "template")}),
        ("Cycle & Order", {"fields": ("cycle_number", "order_shown", "task_kind", "status")}),
        ("Economics (snapshotted)", {"fields": ("price_used", "commission_used")}),
        ("Proof", {"fields": ("proof_text", "proof_link")}),
        ("Timestamps", {
            "fields": ("created_at", "started_at", "submitted_at", "decided_at"),
            "classes": ("collapse",),
        }),
    )

    actions = [
        "approve_admin_submitted",
        "reject_selected",
        "cancel_selected",
    ]

    # --- Bulk approve ADMIN tasks through model logic ---
    @admin.action(description="Approve SUBMITTED admin tasks (wallet debit+credit, update dashboard)")
    def approve_admin_submitted(self, request, queryset):
        ok = skipped = failed = 0
        qs = queryset.filter(status=UserTask.Status.SUBMITTED, task_kind=UserTask.Kind.ADMIN)
        for ut in qs.select_related("user"):
            try:
                ut.approve_admin(approved_by=request.user)
                ok += 1
            except ValidationError as e:
                failed += 1
                self.message_user(request, f"Task #{ut.pk}: {e}", level=messages.ERROR)
            except Exception as e:
                failed += 1
                self.message_user(request, f"Task #{ut.pk}: {e}", level=messages.ERROR)
        skipped = queryset.count() - ok - failed
        if ok:
            self.message_user(request, f"Approved {ok} admin task(s).", level=messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Skipped {skipped} non-eligible task(s).", level=messages.INFO)
        if failed:
            self.message_user(request, f"Failed {failed} task(s). Check errors above.", level=messages.ERROR)

    # --- Guard manual form edits: route ADMIN SUBMITTED -> APPROVED through approve_admin() ---
    def save_model(self, request, obj, form, change):
        if change:
            try:
                old = UserTask.objects.only("status", "task_kind").get(pk=obj.pk)
            except UserTask.DoesNotExist:
                old = None
            if (
                old
                and old.task_kind == UserTask.Kind.ADMIN
                and old.status == UserTask.Status.SUBMITTED
                and obj.status == UserTask.Status.APPROVED
            ):
                # revert, then approve via model method so wallet/dashboard update correctly
                obj.status = old.status
                obj.save(update_fields=["status"])
                try:
                    obj.approve_admin(approved_by=getattr(request, "user", None))
                    self.message_user(request, f"Task #{obj.pk} approved via model flow.", level=messages.SUCCESS)
                except Exception as e:
                    self.message_user(request, f"Approve failed: {e}", level=messages.ERROR)
                return
        super().save_model(request, obj, form, change)

    # --- Reject / Cancel helpers ---
    @admin.action(description="Reject selected tasks (set REJECTED)")
    def reject_selected(self, request, queryset):
        now = timezone.now()
        updated = 0
        for ut in queryset:
            if ut.status in (UserTask.Status.SUBMITTED, UserTask.Status.IN_PROGRESS, UserTask.Status.PENDING):
                ut.status = UserTask.Status.REJECTED
                ut.submitted_at = ut.submitted_at or now
                ut.decided_at = now
                ut.save(update_fields=["status", "submitted_at", "decided_at", "updated_at"])
                updated += 1
        if updated:
            self.message_user(request, f"Rejected {updated} task(s).", level=messages.SUCCESS)
        else:
            self.message_user(request, "No tasks were eligible to reject.", level=messages.INFO)

    @admin.action(description="Cancel selected tasks (set CANCELED)")
    def cancel_selected(self, request, queryset):
        now = timezone.now()
        updated = 0
        for ut in queryset:
            if ut.status not in (UserTask.Status.APPROVED, UserTask.Status.CANCELED):
                ut.status = UserTask.Status.CANCELED
                ut.decided_at = now
                ut.save(update_fields=["status", "decided_at", "updated_at"])
                updated += 1
        if updated:
            self.message_user(request, f"Canceled {updated} task(s).", level=messages.SUCCESS)
        else:
            self.message_user(request, "No tasks were eligible to cancel.", level=messages.INFO)




# ======================
# Task Settings (singleton)
# ======================
# add this so that it can shiw in admin cycles_between_withdrawals
class TaskSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Task cycle", {
            "fields": ("task_limit_per_cycle", "block_on_reaching_limit", "block_message"),
        }),
        ("Per-task amounts", {
            "fields": ("task_price", "task_commission"),
        }),
        ("Trial bonus behavior", {
            "fields": ("clear_trial_bonus_at_limit",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
    readonly_fields = ("created_at", "updated_at")

    def has_add_permission(self, request):
        return not TaskSettings.objects.exists()

    def changelist_view(self, request, extra_context=None):
        obj = TaskSettings.load()
        url = reverse(f"admin:{TaskSettings._meta.app_label}_{TaskSettings._meta.model_name}_change", args=[obj.pk])
        return HttpResponseRedirect(url)

admin.site.register(TaskSettings, TaskSettingsAdmin)


# ======================
# UserTaskTemplate admin
# ======================
class UserTaskTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "hotel_name", "city", "country",
        "status", "is_admin_task",
        "task_date", "task_price", "task_commission",
        "task_label", "task_score",
        "task_id", "cover_thumb",
    )
    list_filter = ("status", "is_admin_task", "task_label", "country", "city", "task_date")
    search_fields = ("hotel_name", "slug", "task_id", "country", "city")
    readonly_fields = ("task_id", "created_at", "updated_at")
    prepopulated_fields = {"slug": ("hotel_name",)}
    ordering = ("-updated_at", "-created_at")
    list_per_page = 50

    fieldsets = (
        ("Basic Info", {"fields": ("hotel_name", "slug", "status", "is_admin_task")}),
        ("Location", {"fields": ("country", "city")}),
        ("Media", {"fields": ("cover_image_url", "cover_image")}),
        ("Money", {"fields": ("task_price", "task_commission")}),
        ("Labels & Scores", {"fields": ("task_label", "task_score", "task_date")}),
        ("Audit", {
            "fields": ("created_by", "task_id", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    def cover_thumb(self, obj):
        url = obj.cover_image_url or (obj.cover_image.url if obj.cover_image else "")
        if not url:
            return "-"
        return format_html('<img src="{}" style="height:40px;border-radius:6px;" />', url)
    cover_thumb.short_description = "Cover"

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.created_by_id and request.user.is_authenticated:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    actions = ["mark_active", "mark_paused", "mark_archived"]

    @admin.action(description="Mark selected as ACTIVE")
    def mark_active(self, request, queryset):
        queryset.update(status="ACTIVE")

    @admin.action(description="Mark selected as PAUSED")
    def mark_paused(self, request, queryset):
        queryset.update(status="PAUSED")

    @admin.action(description="Mark selected as ARCHIVED")
    def mark_archived(self, request, queryset):
        queryset.update(status="ARCHIVED")

admin.site.register(UserTaskTemplate, UserTaskTemplateAdmin)


# ======================
# UserTaskProgress admin
# ======================
@admin.register(UserTaskProgress)
class UserTaskProgressAdmin(admin.ModelAdmin):
    list_display = (
        "user", "cycles_completed", "current_task_index",
        "limit_snapshot", "is_blocked", "updated_at",
    )
    list_filter = ("is_blocked",)
    search_fields = ("user__username", "user__email", "user__phone", "user__nickname")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "last_reset_at")
    ordering = ("-updated_at",)
    list_per_page = 50

    actions = ["start_new_cycle_action"]

    @admin.action(description="Start new cycle (unblock & refresh snapshots)")
    def start_new_cycle_action(self, request, queryset):
        ok = failed = 0
        for prog in queryset.select_related("user"):
            try:
                prog.unblock()
                ok += 1
            except Exception:
                failed += 1
        if ok:
            self.message_user(request, f"Started new cycle for {ok} user(s).", level=messages.SUCCESS)
        if failed:
            self.message_user(request, f"Failed on {failed} user(s). Check logs.", level=messages.ERROR)


# ======================
# CustomUser admin
# ======================
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

        if self.cleaned_data.get("clear_tx_pin"):
            if hasattr(user, "clear_tx_pin") and callable(user.clear_tx_pin):
                user.clear_tx_pin()
            else:
                user.tx_pin_hash = ""

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
    add_form = forms.ModelForm
    form = CustomUserChangeForm
    model = CustomUser

    @admin.display(description="Avatar")
    def avatar_preview(self, obj):
        url = getattr(obj, "display_avatar", None)
        if not url:
            return "—"
        return format_html(
            '<img src="{}" style="width:32px;height:32px;border-radius:50%;object-fit:cover;display:block;" alt="avatar" />',
            url,
        )

    list_display = (
        "id", "avatar_preview", "phone", "nickname",
        "signup_ip", "signup_country", "last_login_ip", "last_login_country",
        "invitation_code",
        "is_active", "is_staff", "date_joined",

    )
    list_display_links = ("id", "phone")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = (
        "username", "email",
        "phone", "nickname",
        "invitation_code",
        "signup_ip", "signup_country", "last_login_ip", "last_login_country",
    )
    ordering = ("-date_joined",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("nickname", "avatar", "avatar_url", "avatar_preview")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
        ("Extras", {"fields": ("invitation_code", "signup_ip", "signup_country", "last_login_ip", "last_login_country")}),
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
    @admin.action(description="Impersonate selected user")
    def impersonate_selected(self, request, queryset):
        if queryset.count() != 1:
            messages.error(request, "Select exactly ONE user.")
            return
        user = queryset.first()
        return redirect(reverse("impersonate", args=[user.pk]))

    actions = ["impersonate_selected"]


# ======================
# Country / Hotel / Favorite
# ======================
@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("name", "iso", "flag")
    search_fields = ("name", "iso")
    ordering = ("name",)


class HotelAdminForm(forms.ModelForm):
    class Meta:
        model = Hotel
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "slug" in self.fields:
            self.fields["slug"].disabled = True
            self.fields["slug"].required = False


@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    form = HotelAdminForm
    list_display = (
        "name", "country", "city", "score", "label",
        "is_recommended", "popularity", "is_published",
        "created_at", "favorites_count",
    )
    list_filter = ("label", "is_recommended", "is_published", "country")
    search_fields = ("name", "city", "description_short", "slug")
    readonly_fields = ("created_at", "favorites_count")
    ordering = ("-created_at",)
    autocomplete_fields = ("country",)
    prepopulated_fields = {"slug": ("name",)}

    def save_model(self, request, obj, form, change):
        from .models import unique_slugify
        if not change:
            obj.slug = unique_slugify(obj, obj.name)
        else:
            if "name" in form.changed_data:
                obj.slug = unique_slugify(obj, obj.name)
        super().save_model(request, obj, form, change)


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "hotel", "created_at")
    search_fields = ("user__phone", "user__username", "hotel__name")
    list_filter = ("created_at",)
    autocomplete_fields = ("user", "hotel")


# ======================
# Wallet & Ledger
# ======================
class HasTrialBonusFilter(SimpleListFilter):
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


@admin.action(description="Grant €300 trial bonus to selected (only if not already granted)")
def grant_trial_bonus(modeladmin, request, queryset):
    bonus_eur = int(getattr(settings, "TRIAL_BONUS_EUR", 300))
    if not getattr(settings, "TRIAL_BONUS_ENABLED", True) or bonus_eur <= 0:
        messages.error(request, "Trial bonus is disabled in settings.")
        return
    bonus_cents = bonus_eur * 100
    qs = queryset.filter(trial_bonus_at__isnull=True)
    granted = 0
    for w in qs.select_for_update():
        with transaction.atomic():
            updated = Wallet.objects.filter(pk=w.pk, trial_bonus_at__isnull=True).update(
                bonus_cents=F("bonus_cents") + bonus_cents,
                trial_bonus_at=timezone.now(),
            )
            if not updated:
                continue
            granted += 1
            try:
                WalletTxn.objects.create(
                    wallet=w,
                    amount_cents=bonus_cents,
                    kind="BONUS",
                    bucket="BONUS",
                    memo="Admin: signup trial bonus",
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
            getattr(u, "phone", ""),
            f"{w.balance_cents / 100:.2f}",
            f"{w.bonus_cents / 100:.2f}",
            f"{(w.balance_cents + w.bonus_cents) / 100:.2f}",
            f"{w.pending_cents / 100:.2f}",
            w.trial_bonus_at.isoformat() if w.trial_bonus_at else "",
        ])
    return resp


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "cash_eur_col",
        "bonus_eur_col",
        "total_eur_col",
        "pending_eur_col",
        "trial_bonus_at",
    )
    list_display_links = ("user",)
    list_filter = (HasTrialBonusFilter,)
    search_fields = ("user__username", "user__email", "user__phone", "user__id")
    autocomplete_fields = ("user",)
    ordering = ("-balance_cents",)
    list_select_related = ("user",)
    readonly_fields = ("trial_bonus_at",)
    actions = [grant_trial_bonus, export_wallets_csv]
    empty_value_display = "—"

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


# ======================
# Payout / Withdraw / Deposit
# ======================
@admin.register(PayoutAddress)
class PayoutAddressAdmin(admin.ModelAdmin):
    list_display = ("user", "address_type", "address", "is_verified", "created_at")
    list_filter = ("address_type", "is_verified")
    search_fields = ("user__phone", "user__username", "address")
    autocomplete_fields = ("user",)


@admin.action(description="Confirm selected withdrawals (mark confirmed)")
def mark_withdrawals_completed(modeladmin, request, queryset):
    done = 0
    for w in queryset:
        try:
            if hasattr(w, "mark_as_confirmed"):
                w.mark_as_confirmed()
                done += 1
        except Exception:
            pass
    if done:
        messages.success(request, f"Confirmed {done} withdrawal(s).")
    else:
        messages.info(request, "No withdrawals were confirmed.")


@admin.action(description="Fail selected withdrawals (mark failed)")
def mark_withdrawals_failed(modeladmin, request, queryset):
    done = 0
    now = timezone.now()
    for w in queryset:
        try:
            if getattr(w, "status", "") != "failed":
                w.status = "failed"
                if hasattr(w, "confirmed_at"):
                    w.confirmed_at = None
                w.save(update_fields=["status", "confirmed_at"] if hasattr(w, "confirmed_at") else ["status"])
                done += 1
        except Exception:
            pass
    if done:
        messages.warning(request, f"Marked {done} withdrawal(s) as failed.")
    else:
        messages.info(request, "No withdrawals were changed.")


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "amount_display", "currency",
        "status_badge", "address", "created_at",
        "processed_at_display",
        "txid_short",
    )
    list_filter = ("status", "currency", "created_at")
    search_fields = ("id", "user__phone", "user__email", "user__username", "txid", "address__address")
    actions = [mark_withdrawals_completed, mark_withdrawals_failed]
    readonly_fields = ("created_at", "processed_at_display")

    def amount_display(self, obj):
        try:
            return f"{obj.amount:.2f}"
        except Exception:
            return f"{obj.amount_cents/100:.2f}"
    amount_display.short_description = "Amount"

    def status_badge(self, obj):
        colors = {
            "pending":   "#f59e0b",
            "confirmed": "#10b981",
            "failed":    "#ef4444",
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
        return getattr(obj, "confirmed_at", "") or ""
    processed_at_display.short_description = "Confirmed at"


@admin.register(DepositAddress)
class DepositAddressAdmin(admin.ModelAdmin):
    list_display = ("network", "address", "active", "updated_at")
    list_filter = ("network", "active")
    search_fields = ("address",)


@admin.action(description="Confirm & credit selected deposits")
def admin_confirm_deposits(modeladmin, request, queryset):
    """
    Drop-in replacement for old service: mark deposit confirmed and credit user's wallet.
    """
    ok = failed = 0
    for dep in queryset.select_related("user"):
        try:
            if dep.status == "confirmed":
                continue
            dep.status = "confirmed"
            dep.confirmed_at = timezone.now()
            dep.save(update_fields=["status", "confirmed_at", "updated_at"] if hasattr(dep, "updated_at") else ["status", "confirmed_at"])

            # Credit wallet with deposit amount
            wallet = dep.user.wallet
            wallet.credit(dep.amount_cents, bucket="CASH", kind="DEPOSIT", memo=f"Deposit {dep.reference}")
            ok += 1
        except Exception:
            failed += 1
    if ok:
        messages.success(request, f"Confirmed & credited {ok} deposit(s).")
    if failed:
        messages.error(request, f"Failed {failed} deposit(s). Check logs.")

@admin.register(DepositRequest)
class DepositRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "reference", "amount_cents", "currency", "network", "status", "created_at")
    list_filter = ("status", "currency", "network")
    search_fields = ("reference", "user__phone", "user__email", "user__username")
    autocomplete_fields = ("user",)
    actions = [admin_confirm_deposits]


# ======================
# Info / Announcement
# ======================
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
