# support_app/signals.py
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Agent


User = get_user_model()

@receiver(post_save, sender=User)
def create_or_cleanup_agent(sender, instance, created, **kwargs):
    """
    Ensure staff users have an Agent profile; remove if staff flag is turned off.
    """
    if instance.is_staff:
        Agent.objects.get_or_create(user=instance)
    else:
        Agent.objects.filter(user=instance).delete()

def _set_agent_status(user, status: str):
    try:
        if not getattr(user, "is_staff", False):
            return
        ag = user.agent_profile  # OneToOne
        if ag.status != status:
            ag.status = status
            ag.save(update_fields=["status"])
    except Agent.DoesNotExist:
        pass

@receiver(user_logged_in)
def _agent_online(sender, user, request, **kwargs):
    _set_agent_status(user, "online")

@receiver(user_logged_out)
def _agent_offline(sender, user, request, **kwargs):
    _set_agent_status(user, "offline")

