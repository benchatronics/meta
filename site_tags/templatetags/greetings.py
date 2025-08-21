from django import template
from django.utils import timezone
from zoneinfo import ZoneInfo

register = template.Library()

def _greet(hour: int) -> str:
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 21:
        return "Good evening"
    return "Good night"

@register.simple_tag(takes_context=True)
def time_greeting(context):
    request = context.get("request")
    # If you store per-user timezone, prefer that; else use Django's current
    user_tz = None
    if request and hasattr(request.user, "timezone"):
        tz_attr = getattr(request.user, "timezone", None)  # could be a string or object
        user_tz = tz_attr.key if hasattr(tz_attr, "key") else (tz_attr or None)

    now_local = timezone.localtime(timezone.now(), ZoneInfo(user_tz)) if user_tz else timezone.localtime()
    return _greet(now_local.hour)
