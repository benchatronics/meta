#notifications.py
import os, requests
from django.core.mail import send_mail
from django.conf import settings

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_email(subject: str, body: str, to_list: list[str]):
    if not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD:
        return False
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to_list, fail_silently=True)
        return True
    except Exception:
        return False

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True})
        return r.ok
    except Exception:
        return False

def notify_waiting_agent(session, preview_msg=""):
    link = f"{settings.CHAT_API_ORIGIN}/admin/support/sessions/{session.id}/"
    subject = f"[Support] Waiting for Agent • Session #{session.id}"
    body = f"Session #{session.id} requires a human.\nTopic: {session.topic or '-'}\nPreview: {preview_msg}\nJoin: {link}"
    email_ok = send_email(subject, body, [settings.EMAIL_HOST_USER])
    tg_ok = send_telegram(f"🟡 Waiting for Agent\nSession #{session.id}\n{preview_msg}\nJoin: {link}")
    return email_ok or tg_ok
