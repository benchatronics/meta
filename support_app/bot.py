from __future__ import annotations

import os
BOT_ENABLED = os.getenv("BOT_ENABLED", "true").lower() == "true"
BOT_NAME = "Orbitpedia Assistant"

DEFAULT_GREET = (
    "Hi! I’m Orbitpedia Assistant. I can answer most questions and guide you. "
    "If you’d like a human, click “Talk to a human” and an agent will join."
)

def initial_greeting():
    return DEFAULT_GREET

def answer(user_text: str, context: dict | None = None) -> tuple[str, bool]:
    t = (user_text or "").lower()
    if any(k in t for k in ["refund","chargeback","cancel","cancellation"]):
        return ("For refunds/cancellations, please share your booking ID (no personal data). "
                "Policies vary by fare/provider—we’ll check and confirm next steps.", False)
    if any(k in t for k in ["change","reschedule","date change"]):
        return ("To change a booking, share your booking ID and preferred date/time; I’ll outline the options.", False)
    if any(k in t for k in ["payment","failed","card","invoice"]):
        return ("Payment issues: I can guide you through retry steps or alternatives. What error are you seeing?", False)
    return ("I can help with bookings, refunds, changes, payments, and account support. Tell me what you need.", False)
