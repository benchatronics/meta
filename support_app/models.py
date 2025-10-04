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

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["session", "id"]),
        ]


class Event(models.Model):
    KIND = [
        ("agent_requested", "Agent requested"),
        ("agent_joined", "Agent joined"),
        ("bot_suppressed", "Bot suppressed"),
        ("resolved", "Resolved"),
        ("reopened", "Reopened"),
        ("email_sent", "Email sent"),
        ("telegram_sent", "Telegram sent"),
    ]
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=40, choices=KIND)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
