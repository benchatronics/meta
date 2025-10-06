#support_app/urls.py
from django.urls import path
from . import views_public as pub
from . import views_admin as adm

urlpatterns = [
    # Public API
    path("api/chat/start", pub.start),
    path("api/chat/messages", pub.messages),
    path("api/chat/send", pub.send),
    path("api/chat/agent-request", pub.agent_request),
    path("api/chat/heartbeat", pub.heartbeat),
    path("api/chat/end", pub.end),

    #delete chat messages
    path("support/delete-message", adm.delete_message, name="support_delete_message"),
    path("support/delete-chat", adm.delete_chat, name="support_delete_chat"),

    path("support/leave", adm.leave, name="support_leave"),


    # Support (staff-only pages & JSON) — lives at /support/...
    path("support/queue/", adm.queue_page, name="support_queue"),
    path("support/dashboard/", adm.dashboard_page, name="support_dashboard"),
    path("support/sessions/<int:session_id>/", adm.session_page, name="support_session"),

    path("support/queue.json", adm.queue, name="support_queue_json"),
    path("support/sessions.json", adm.sessions_json, name="support_sessions_json"),
    path("support/join", adm.join, name="support_join"),
    path("support/send", adm.send, name="support_send"),
    path("support/resolve", adm.resolve, name="support_resolve"),

]
