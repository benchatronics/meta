# support_app/models.py
from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class Agent(models.Model):
    """
    Mirrors a staff CustomUser. Public label comes from CustomUser.display_name (nickname/phone).
    Auto-created via signals when a user is_staff.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="agent_profile")
    status = models.CharField(
        max_length=12,
        default="offline",
        choices=[("online", "Online"), ("away", "Away"), ("offline", "Offline")],
    )
    max_concurrent = models.PositiveIntegerField(default=3)

    def __str__(self):
        return self.display_nickname

    @property
    def display_nickname(self) -> str:
        u = self.user
        # Your CustomUser already has display_name property
        return getattr(u, "display_name", None) or getattr(u, "nickname", "") or getattr(u, "phone", "")

    @property
    def email(self) -> str:
        return getattr(self.user, "email", "") or ""


class Tag(models.Model):
    name = models.CharField(max_length=40, unique=True)

    def __str__(self):
        return self.name


class ChatSession(models.Model):
    STATUS = [
        ("open", "Open"),
        ("waiting_agent", "Waiting agent"),
        ("agent_joined", "Agent joined"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
        ("abandoned", "Abandoned"),
    ]

    channel = models.CharField(max_length=20, default="web")
    # ONE status field (db_index so dashboards are fast)
    status = models.CharField(max_length=32, choices=STATUS, default="open", db_index=True)

    # Who is this?
    visitor_id = models.CharField(max_length=64, db_index=True)                 # device/browser fingerprint
    user_ref   = models.CharField(max_length=64, blank=True, null=True, db_index=True)  # stable string key
    user       = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    # Routing & context
    agent   = models.ForeignKey("Agent", null=True, blank=True, on_delete=models.SET_NULL)
    topic   = models.CharField(max_length=120, blank=True)
    priority = models.CharField(
        max_length=20,
        default="normal",
        choices=[("normal", "Normal"), ("high", "High")],
    )

    # Labels & telemetry
    tags = models.ManyToManyField(Tag, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_user_heartbeat = models.DateTimeField(null=True, blank=True)
    user_typing_until   = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["updated_at"]),
            models.Index(fields=["status", "updated_at"]),
        ]

    def __str__(self):
        return f"Session #{self.id} ({self.status})"

class Message(models.Model):
    SENDER = [("user", "User"), ("bot", "Bot"), ("agent", "Agent"), ("system", "System")]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    sender_type = models.CharField(max_length=10, choices=SENDER)
    author = models.CharField(max_length=80)  # "Guest", "Agent • nick", "Orbitpedia Assistant"
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    visibility = models.CharField(
        max_length=20,
        default="public",
        choices=[("public", "Public"), ("internal_note", "Internal note")],
    )

    # NEW: client-provided idempotency token to prevent dupes (per session)
    client_nonce = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["session", "id"]),
            # NEW: speeds up dedupe checks by (session, client_nonce)
            models.Index(fields=["session", "client_nonce"]),
        ]
        constraints = [
            # NEW: enforce uniqueness of nonce within a session (only when nonce is not NULL)
            models.UniqueConstraint(
                fields=["session", "client_nonce"],
                name="uniq_session_client_nonce",
                condition=~models.Q(client_nonce=None),
            ),
        ]

from django.db import models

class Event(models.Model):
    class Kind(models.TextChoices):
        AGENT_REQUESTED = "agent_requested", "Agent requested"
        AGENT_JOINED    = "agent_joined",    "Agent joined"
        AGENT_LEFT      = "agent_left",      "Agent left"          # ✅ new
        BOT_SUPPRESSED  = "bot_suppressed",  "Bot suppressed"
        RESOLVED        = "resolved",        "Resolved"
        REOPENED        = "reopened",        "Reopened"
        EMAIL_SENT      = "email_sent",      "Email sent"
        TELEGRAM_SENT   = "telegram_sent",   "Telegram sent"

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="events",
    )
    kind = models.CharField(
        max_length=40,
        choices=Kind.choices,
    )
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]  # events naturally in timeline order
        indexes = [
            models.Index(fields=["session", "id"]),
            models.Index(fields=["session", "created_at"]),
            models.Index(fields=["kind", "created_at"]),
        ]

    def __str__(self):
        return f"Event({self.kind}) • session #{self.session_id}"
