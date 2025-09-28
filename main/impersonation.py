from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model, login
from django.contrib.auth.backends import ModelBackend
from django.shortcuts import redirect
from django.contrib import messages

User = get_user_model()

def _can_impersonate(hijacker, hijacked):
    if not hijacker.is_authenticated or hijacker.pk == hijacked.pk:
        return False
    # superusers can impersonate anyone (incl. staff/superusers)
    if hijacker.is_superuser:
        return True
    # staff can impersonate only normal active users
    if hijacker.is_staff and hijacked.is_active and not hijacked.is_staff and not hijacked.is_superuser:
        return True
    return False

@staff_member_required
def impersonate(request, user_id: int):
    target = User.objects.filter(pk=user_id).first()
    if not target:
        messages.error(request, "User not found.")
        return redirect("/admin/")
    if not _can_impersonate(request.user, target):
        messages.error(request, "Not allowed to impersonate this user.")
        return redirect("/admin/")

    # remember who is impersonating
    orig_admin_id = request.user.pk

    # switch login
    login(request, target, backend="django.contrib.auth.backends.ModelBackend")

    # set flags AFTER login to survive session rotation
    if not request.session.get("_orig_admin_id"):
        request.session["_orig_admin_id"] = orig_admin_id
    request.session["impersonating"] = True
    request.session.modified = True

    return redirect("/")   # redirect to dashboard or homepage

@staff_member_required
def release_impersonation(request):
    request.session.pop("impersonating", None)
    orig_id = request.session.pop("_orig_admin_id", None)
    request.session.modified = True

    if not orig_id:
        messages.info(request, "No impersonation in progress.")
        return redirect("/admin/")

    admin_user = User.objects.filter(pk=orig_id).first()
    if not admin_user:
        messages.error(request, "Original admin not found; please log in again.")
        return redirect("/admin/login/")

    login(request, admin_user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("/admin/")
