# notifications.py
from __future__ import annotations

import os
from typing import Iterable, List

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils.html import escape

from .models import Agent, ChatSession

# Optional: Telegram fallback via env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# Comma-separated list in settings or env for default recipients
# e.g. SUPPORT_AGENT_EMAILS="ops@yourco.com,support@yourco.com"
DEFAULT_AGENT_EMAILS = getattr(settings, "SUPPORT_AGENT_EMAILS", []) or [
    e.strip() for e in os.getenv("SUPPORT_AGENT_EMAILS", "").split(",") if e.strip()
]

# How long to suppress duplicate notifications for the same session (seconds)
NOTIFY_DEDUP_TTL = int(getattr(settings, "SUPPORT_NOTIFY_DEDUP_TTL", 300))  # default 5 minutes


def _unique(seq: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for s in seq:
        if not s:
            continue
        key = s.lower()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _agent_emails_from_status() -> List[str]:
    """
    Pull emails for agents who are currently 'online' or 'away'.
    Falls back to empty list if Agent table isn’t populated.
    """
    try:
        qs = Agent.objects.filter(status__in=["online", "away"]).select_related("user")
        emails: List[str] = []
        for a in qs:
            em = getattr(a, "email", "") or getattr(getattr(a, "user", None), "email", "")
            if em:
                emails.append(em)
        return _unique(emails)
    except Exception:
        return []


def _resolve_recipients() -> List[str]:
    """
    Merge: explicit SUPPORT_AGENT_EMAILS + online/away agents + EMAIL_HOST_USER (fallback).
    """
    explicit = DEFAULT_AGENT_EMAILS
    dynamic  = _agent_emails_from_status()
    fallback = [getattr(settings, "EMAIL_HOST_USER", "")] if getattr(settings, "EMAIL_HOST_USER", "") else []
    return _unique([*explicit, *dynamic, *fallback])


def send_email(subject: str, body_text: str, to_list: List[str], html_body: str | None = None) -> bool:
    """
    Sends plain-text email (and HTML if provided). Returns True if at least one attempt didn’t raise.
    """
    if not getattr(settings, "EMAIL_BACKEND", ""):
        return False

    sender = getattr(settings, "DEFAULT_FROM_EMAIL", None) or getattr(settings, "EMAIL_HOST_USER", None)
    if not sender:
        return False

    try:
        send_mail(
            subject,
            body_text,
            sender,
            to_list,
            fail_silently=True,
            html_message=html_body,
        )
        return True
    except Exception:
        return False


def send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=8,
        )
        return bool(r.ok)
    except Exception:
        return False


def notify_waiting_agent(session: ChatSession, preview_msg: str = "") -> bool:
    """
    Called when a user clicks “Talk to a human”.

    - Dedupe: suppress duplicate alerts for this session for NOTIFY_DEDUP_TTL seconds.
    - Sends email to resolved recipients.
    - Telegram fallback if configured.
    Returns True if any channel reported success.
    """
    if not isinstance(session, ChatSession):
        return False

    # Dedupe notifications per-session for a short window
    dedup_key = f"support:wait_alert:{session.id}"
    if cache.get(dedup_key):
        return True
    cache.set(dedup_key, "1", NOTIFY_DEDUP_TTL)

    site_name   = getattr(settings, "SITE_NAME", "Orbitpedia")
    base_origin = getattr(settings, "CHAT_API_ORIGIN", "").rstrip("/")
    admin_link  = f"{base_origin}/support/sessions/{session.id}/" if base_origin else f"/support/sessions/{session.id}/"

    topic   = (session.topic or "-").strip()
    preview = (preview_msg or "").strip()

    subject = f"[{site_name}] Waiting for Agent • Session #{session.id}"
    text_lines = [
        f"Session #{session.id} requires a human.",
        f"Topic: {topic}",
        f"Preview: {preview}" if preview else "Preview: —",
        f"Join: {admin_link}",
    ]
    body_text = "\n".join(text_lines)

    # Simple HTML body (escape user text)
    html = (
        "<div style='font:14px system-ui,Segoe UI,Roboto,Arial'>"
        f"<p><strong>Session #{session.id}</strong> requires a human.</p>"
        f"<p><b>Topic:</b> {escape(topic) or '-'}</p>"
        f"<p><b>Preview:</b> {escape(preview) or '—'}</p>"
        f"<p><a href='{escape(admin_link)}' target='_blank' rel='noopener'>Join session</a></p>"
        "</div>"
    )

    recipients = _resolve_recipients()
    email_ok = False
    if recipients:
        email_ok = send_email(subject, body_text, recipients, html_body=html)

    # --- Telegram message (syntax-safe) ---
    tg_lines = [
        "🟡 Waiting for Agent",
        f"{site_name} — Session #{session.id}",
    ]
    if topic:
        tg_lines.append(f"Topic: {topic}")
    if preview:
        tg_lines.append(f"Preview: {preview}")
    tg_lines.append(f"Join: {admin_link}")
    tg_ok = send_telegram("\n".join(tg_lines))

    return bool(email_ok or tg_ok)
