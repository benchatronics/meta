from django.conf import settings
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

_client = None

def _client_once() -> Client:
    """
    Initialize Twilio client once. (Optional: honor Regions if configured.)
    """
    global _client
    if _client is None:
        if settings.TWILIO_REGION:
            _client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, region=settings.TWILIO_REGION)
        else:
            _client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    return _client

def send_sms(phone_e164: str, message: str) -> str:
    """
    Sends a plain SMS via Twilio. Returns the message SID on success.
    Raises TwilioRestException on failure.
    """
    if not settings.TWILIO_FROM_SMS:
        raise RuntimeError("TWILIO_FROM_SMS is not configured.")
    client = _client_once()
    msg = client.messages.create(
        body=message,
        from_=settings.TWILIO_FROM_SMS,
        to=phone_e164,
    )
    return msg.sid
