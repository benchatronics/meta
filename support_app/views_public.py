# views_public.py
from __future__ import annotations

import json
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now
from django.db import models  # for JSONField lookups (metadata__user_ref)

from .models import ChatSession, Message, Event
from . import bot
from .notifications import notify_waiting_agent


def _payload_session(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "status": s.status,
        "agent": s.agent.display_nickname if s.agent else None,
        "topic": s.topic,
        "priority": s.priority,
    }


@csrf_exempt
@require_http_methods(["POST"])
def start(request):
    """
    Start (or resume) a chat session.

    Reuse priority:
      1) Authenticated request.user's active session
      2) Active session with the same user_ref (string you pass from the widget)
      3) Active session with the same visitor_id (browser-local id)

    Active statuses: open / waiting_agent / agent_joined
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    # Safely coerce all inputs to strings (user_ref may be numeric from templates)
    def _s(val) -> str:
        if val is None:
            return ""
        try:
            return str(val).strip()
        except Exception:
            return ""

    visitor_id = _s(data.get("visitor_id"))
    topic      = _s(data.get("topic"))
    user_ref   = _s(data.get("user_ref"))

    if not visitor_id:
        return HttpResponseBadRequest("missing visitor_id")

    active_statuses = ("open", "waiting_agent", "agent_joined")

    # 1) If authenticated, prefer a session bound to this user
    sess = None
    if request.user.is_authenticated:
        sess = (
            ChatSession.objects
            .filter(user=request.user, status__in=active_statuses)
            .order_by("-updated_at")
            .first()
        )

    # 2) Else, reuse by user_ref (if provided)
    if not sess and user_ref:
        sess = (
            ChatSession.objects
            .filter(status__in=active_statuses)
            .filter(models.Q(metadata__user_ref=user_ref))
            .order_by("-updated_at")
            .first()
        )

    # 3) Else, reuse by visitor_id (legacy, browser-local)
    if not sess:
        sess = (
            ChatSession.objects
            .filter(visitor_id=visitor_id, status__in=active_statuses)
            .order_by("-updated_at")
            .first()
        )

    # Create a new session if none found
    if not sess:
        sess = ChatSession.objects.create(visitor_id=visitor_id, topic=topic)

        changed = False
        if request.user.is_authenticated and not sess.user_id:
            sess.user = request.user
            changed = True

        if user_ref:
            md = dict(sess.metadata or {})
            md["user_ref"] = user_ref
            sess.metadata = md
            changed = True

        if changed:
            sess.save(update_fields=["user", "metadata", "updated_at"])

        if bot.BOT_ENABLED:
            Message.objects.create(
                session=sess,
                sender_type="bot",
                author=bot.BOT_NAME,
                body=bot.initial_greeting(),
            )
    else:
        # If we reused a session, ensure user/user_ref are attached if missing/outdated
        changed = False

        if request.user.is_authenticated and not sess.user_id:
            sess.user = request.user
            changed = True

        current_ref = (sess.metadata or {}).get("user_ref")
        if user_ref and current_ref != user_ref:
            md = dict(sess.metadata or {})
            md["user_ref"] = user_ref
            sess.metadata = md
            changed = True

        if changed:
            sess.save(update_fields=["user", "metadata", "updated_at"])

    return JsonResponse({"session": _payload_session(sess)})


@require_http_methods(["GET"])
def messages(request):
    session_id = request.GET.get("session")
    try:
        after_id = int(request.GET.get("after_id") or 0)
    except (TypeError, ValueError):
        after_id = 0

    if not session_id:
        return HttpResponseBadRequest("missing session")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    # Explicit order; cap results
    qs = sess.messages.filter(id__gt=after_id).order_by("id")[:200]

    data = [
        {
            "id": m.id,
            "sender_type": m.sender_type,
            "author": m.author,
            "body": m.body,
            "created_at": m.created_at.isoformat(),
        }
        for m in qs
    ]

    return JsonResponse(
        {
            "messages": data,
            "status": sess.status,
            "agent": sess.agent.display_nickname if sess.agent else None,
            "agent_joined": bool(sess.status == "agent_joined"),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def send(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    author = (str(data.get("author") or "Guest")).strip()[:80]
    body = (str(data.get("body") or "")).strip()

    if not session_id or not body:
        return HttpResponseBadRequest("missing fields")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    Message.objects.create(session=sess, sender_type="user", author=author, body=body)

    # If a human is requested or joined, suppress bot
    if sess.status in ("waiting_agent", "agent_joined"):
        return JsonResponse({"ok": True})

    if bot.BOT_ENABLED:
        reply, solved = bot.answer(body, context={"topic": sess.topic})
        Message.objects.create(session=sess, sender_type="bot", author=bot.BOT_NAME, body=reply)
        if not solved:
            Message.objects.create(
                session=sess,
                sender_type="bot",
                author=bot.BOT_NAME,
                body='If you’d like to talk to a human, click “Talk to a human”.',
            )

    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST"])
def agent_request(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    if not session_id:
        return HttpResponseBadRequest("missing session")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    if sess.status != "agent_joined":
        sess.status = "waiting_agent"
        sess.save(update_fields=["status", "updated_at"])

        Event.objects.create(session=sess, kind="agent_requested", payload={})

        last = sess.messages.order_by("-id").first()
        preview = (last.body[:180] + "…") if last and last.body else ""
        notify_waiting_agent(sess, preview_msg=preview)

    return JsonResponse({"status": sess.status})


@csrf_exempt
@require_http_methods(["POST"])
def end(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    if not session_id:
        return HttpResponseBadRequest("missing session")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    sess.status = "closed"
    sess.save(update_fields=["status", "updated_at"])

    Message.objects.create(
        session=sess,
        sender_type="system",
        author="System",
        body="User ended the chat.",
    )
    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST"])
def heartbeat(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("invalid JSON")

    session_id = data.get("session")
    if not session_id:
        return HttpResponseBadRequest("missing session")

    try:
        sess = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        return HttpResponseBadRequest("invalid session")

    sess.last_user_heartbeat = now()
    sess.save(update_fields=["last_user_heartbeat", "updated_at"])
    return JsonResponse({"ok": True})
