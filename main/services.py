# services.py
from django.db import transaction
from django.utils import timezone
from .models import DepositRequest, Wallet

@transaction.atomic
def confirm_deposit(dep: DepositRequest) -> bool:
    """
    Idempotently confirm a deposit and credit the user's wallet.
    Returns:
        True  -> we confirmed and credited now
        False -> already confirmed/failed or in an invalid state
    Notes:
        - Locks both DepositRequest and Wallet rows to prevent double-crediting.
        - Accepts transitions from 'draft', 'awaiting_payment', or 'awaiting_review'.
        - Sets verified_at if missing, and always sets confirmed_at on success.
    """
    # Lock the deposit row for update to avoid race conditions
    dep = DepositRequest.objects.select_for_update().select_related("user").get(pk=dep.pk)

    # Do nothing if already finalized or invalid state
    if dep.status in ("confirmed", "failed"):
        return False
    if dep.status not in ("draft", "awaiting_payment", "awaiting_review"):
        return False

    # Lock or create the wallet row
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=dep.user)

    # Credit the wallet (treat None as 0 just in case)
    wallet.balance_cents = (wallet.balance_cents or 0) + (dep.amount_cents or 0)
    wallet.save(update_fields=["balance_cents"])

    # Mark the deposit as confirmed and set timestamps
    now = timezone.now()
    if not dep.verified_at:
        dep.verified_at = now
    dep.confirmed_at = now
    dep.status = "confirmed"
    dep.save(update_fields=["status", "verified_at", "confirmed_at"])

    return True
