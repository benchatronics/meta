from django.core.management.base import BaseCommand
from support_app.models import Tag

DEFAULT_TAGS = [
  "billing","bookings","refund","itinerary","account","payment","verification",
  "technical","voucher","cancellation","change-request","gdpr","priority","vip",
  "resolved-self-serve","resolved-agent","escalated-vendor","awaiting-customer","duplicate",
  "negative-sentiment","low-confidence","policy-check"
]

class Command(BaseCommand):
    help = "Seed default support tags"
    def handle(self, *args, **options):
        for t in DEFAULT_TAGS:
            Tag.objects.get_or_create(name=t)
        self.stdout.write(self.style.SUCCESS("Support tags seeded."))
