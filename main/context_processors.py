def impersonation(request):
    return {
        "is_impersonating": bool(
            request.session.get("impersonating") or
            request.session.get("_orig_admin_id")
        )
    }
