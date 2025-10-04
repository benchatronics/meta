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
