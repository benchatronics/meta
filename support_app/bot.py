from __future__ import annotations

import os
import re

BOT_ENABLED = os.getenv("BOT_ENABLED", "true").lower() == "true"
BOT_NAME = "Orbitpedia Assistant"

# ===== Greeting =====
DEFAULT_GREET = (
    "Hi! I’m Orbitpedia Assistant. I can help you search & book flights, hotels, cars, "
    "and packages; handle date changes, refunds, and payment issues; and manage your itinerary.\n\n"
    "Tip: you can paste a booking ID or describe your trip (e.g., “Paris to New York 20–27 Nov, 2 adults”).\n"
    "If you’d like a human at any point, click “Talk to a human” and an agent will join."
)

def initial_greeting() -> str:
    return DEFAULT_GREET

# ===== Helpers =====
KW = lambda *words: re.compile(r"|".join([re.escape(w) for w in words]), re.I)

K_REFUND = KW("refund", "chargeback", "cancel", "cancellation", "money back", "reimbursement")
K_CHANGE = KW("change", "reschedule", "date change", "modify", "rebook")
K_PAY    = KW("payment", "pay", "failed", "card", "declined", "invoice", "billing", "charge")
K_FLIGHT = KW("flight", "fly", "airline", "fare", "economy", "business", "one-way", "round trip")
K_HOTEL  = KW("hotel", "stay", "room", "check-in", "check out", "property", "guesthouse")
K_CAR    = KW("car", "vehicle", "rental", "hire", "pickup", "dropoff")
K_BUNDLE = KW("package", "bundle", "flight+hotel", "vacation")
K_DOCS   = KW("visa", "passport", "document", "id", "proof", "vaccination")
K_ACC    = KW("account", "profile", "login", "sign in", "password", "2fa", "notification")
K_ITIN   = KW("itinerary", "booking", "PNR", "reservation", "ticket", "confirmation", "booking id")
K_THANKS = KW("thanks", "thank you", "great", "perfect", "nice one", "resolved")
K_GREET  = KW("hi", "hello", "hey", "good morning", "good afternoon", "good evening")

def _qrs(options: list[str]) -> str:
    """Render quick-reply suggestions as text (since widget is text-only)."""
    return "Quick replies: " + " | ".join(f"[{o}]" for o in options)

def _collect_trip_details(prefix: str) -> str:
    return (
        f"{prefix}\n\n"
        "Please share as many as you can:\n"
        "• From → To (cities or airports)\n"
        "• Dates (exact or flexible ±3 days)\n"
        "• Travelers (adults/children)\n"
        "• Cabin/room type & budget\n"
        "• Any airline/hotel preference\n\n"
        + _qrs(["Show flight deals", "Find hotels near city center", "Car for 3 days"])
    )

def _booking_lookup_prompt() -> str:
    return (
        "To pull up a reservation, share:\n"
        "• Booking ID / Confirmation code\n"
        "• Last name on the booking (no other personal data)\n\n"
        + _qrs(["Here’s my booking ID", "I don’t know my ID"])
    )

def _refund_flow() -> str:
    return (
        "Refunds & Cancellations — I can check eligibility.\n"
        "1) Send your Booking ID and last name (no other personal data).\n"
        "2) Tell me if this is a full cancellation or partial (e.g., 1 of 2 rooms, 1 flight segment).\n"
        "3) If you see airline/provider terms, paste them—I’ll parse the policy.\n\n"
        "Notes:\n"
        "• Basic fares are often non-refundable; credits or change fees may apply.\n"
        "• Refund timelines vary by provider and payment method.\n\n"
        + _qrs(["Start refund check", "View cancellation policy", "Talk to a human"])
    )

def _change_flow() -> str:
    return (
        "Date/Time Changes — let’s see options.\n"
        "Please share:\n"
        "• Booking ID + last name\n"
        "• Desired new date/time (or a range)\n"
        "• Flexibility (±days/hours)\n\n"
        "I’ll check rules, fees, and availability, then summarize choices.\n"
        + _qrs(["I have my Booking ID", "Change just the return", "What are the fees?"])
    )

def _payment_flow() -> str:
    return (
        "Payment help — tell me what you’re seeing and we’ll fix it.\n"
        "Common solutions:\n"
        "• Card declined → try 3-D Secure or a different card; confirm billing ZIP/postcode and name.\n"
        "• Payment pending → it may clear in a few minutes; don’t retry too fast.\n"
        "• Need invoice → say “Invoice email + booking ID”.\n\n"
        "If you paste the exact error text, I’ll suggest targeted steps.\n"
        + _qrs(["Retry payment", "Use different card", "Get invoice"])
    )

def _flight_flow() -> str:
    return _collect_trip_details("Flight search — I can filter airlines, cabins, bags, and layovers.")

def _hotel_flow() -> str:
    return _collect_trip_details("Hotel search — share city/area, dates, guests, budget, and must-haves (e.g., pool, breakfast).")

def _car_flow() -> str:
    return (
        "Car rental — please share:\n"
        "• Pickup city/location & date/time\n"
        "• Drop-off location & date/time (same or different)\n"
        "• Driver age (e.g., 25+)\n"
        "• Class (economy/SUV/van) & budget\n\n"
        + _qrs(["SUV this weekend", "Airport pickup", "Add child seat"])
    )

def _bundle_flow() -> str:
    return (
        "Flight + Hotel packages — often cheaper together.\n"
        "Share destination, dates, travelers, and budget; I’ll propose bundles.\n"
        + _qrs(["Bundle Paris→Rome", "City break this month", "Beach + 3-star hotel"])
    )

def _docs_flow() -> str:
    return (
        "Visas & travel documents — I can outline requirements (no legal advice).\n"
        "Tell me: nationality, destination, travel dates, and purpose (tourism/business).\n"
        + _qrs(["Schengen visa info (US passport)", "Transit rules", "Baggage policy"])
    )

def _account_flow() -> str:
    return (
        "Account & login:\n"
        "• Reset password → use ‘Forgot Password’, check spam for email code.\n"
        "• 2FA problems → confirm device time is automatic, try backup code.\n"
        "• Change email/phone → I can note it for the agent; some changes require verification.\n"
        + _qrs(["Reset password", "Change phone", "Delete my account"])
    )

def _itinerary_flow() -> str:
    return _booking_lookup_prompt()

def _menu(topic: str | None = None) -> str:
    t = f" (topic: {topic})" if topic else ""
    return (
        f"What would you like to do{t}?\n"
        "1) Search flights ✈️\n"
        "2) Find hotels 🏨\n"
        "3) Rent a car 🚗\n"
        "4) Flight+Hotel package 🎒\n"
        "5) Change or cancel a booking 🔁\n"
        "6) Payment or invoice 💳\n"
        "7) Check my booking 🧾\n"
        "8) Visa & documents 🛂\n"
        "9) Account & profile 👤\n\n"
        + _qrs([
            "Search flights", "Find hotels", "Car rental",
            "Change booking", "Refund", "Payment help",
            "Find my booking", "Visa info", "Talk to a human"
        ])
    )

# ===== Core Answer Logic =====
def answer(user_text: str, context: dict | None = None) -> tuple[str, bool]:
    """
    Returns (reply_text, solved_bool).
    We intentionally keep solved=False unless the user signals closure.
    """
    text = (user_text or "").strip()
    low = text.lower()
    topic = (context or {}).get("topic") or ""

    # Light intent routing
    if K_THANKS.search(low):
        return ("You’re welcome! If there’s anything else—search, changes, refunds—just tell me the details. "
                "Otherwise you can close the chat anytime.", True)

    if K_ITIN.search(low):
        return (_itinerary_flow(), False)

    if K_REFUND.search(low):
        return (_refund_flow(), False)

    if K_CHANGE.search(low):
        return (_change_flow(), False)

    if K_PAY.search(low):
        return (_payment_flow(), False)

    if K_FLIGHT.search(low) or (" to " in low and re.search(r"\b\d{1,2}\b", low)):
        # crude pattern to catch prompts like "Paris to New York 20–27"
        return (_flight_flow(), False)

    if K_HOTEL.search(low):
        return (_hotel_flow(), False)

    if K_CAR.search(low):
        return (_car_flow(), False)

    if K_BUNDLE.search(low):
        return (_bundle_flow(), False)

    if K_DOCS.search(low):
        return (_docs_flow(), False)

    if K_ACC.search(low):
        return (_account_flow(), False)

    if K_GREET.search(low):
        return (
            "Hello! I can help you plan and manage trips end-to-end.\n\n" +
            _menu(topic), False
        )

    # Booking ID heuristic: alnum 5–8 or common airline PNR patterns
    if re.search(r"\b([A-Z0-9]{5,8})\b", text, re.I):
        # We don't verify here—just prompt for last name to proceed
        return (
            "Got a code—great. Please add the **last name** on the booking so I can open it.\n\n" +
            _qrs(["Last name is …", "Not my booking ID"]), False
        )

    # Fallback: keep them engaged with a helpful menu + EU/US examples
    examples = (
        "Examples you can paste:\n"
        "• “Flight Paris → New York 20–27 Nov, 2 adults, economy, max $700 each”\n"
        "• “Hotel in Berlin, 15–18 Oct, 1 room for 2, budget €120/night, breakfast included”\n"
        "• “Change return date on my booking ABC123 to 25 Nov”\n"
        "• “Card declined with code ‘Do Not Honor’”\n"
    )
    return (
        "I can help with bookings, changes, refunds, payments, and account support.\n\n" +
        _menu(topic) + "\n\n" + examples, False
    )
