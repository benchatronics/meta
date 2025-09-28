# main/management/commands/seed_info.py
from django.core.management.base import BaseCommand
from django.utils import timezone

# Flexible import: works whether you put models in models_info.py or models.py
try:
    from main.models_info import InfoPage, Announcement
except ImportError:
    from main.models import InfoPage, Announcement  # fallback

PAGES = {
    "about": {
        "title": "About us",
        "body": (
            "Orbitpedia (metasearch) is a fast, secure platform for travel and rewards.\n\n"
            "We help you discover hotels, track favorites, and manage your wallet."
        ),
    },
    "contact": {
        "title": "Contact us",
        "body": (
            "Need help? We’re here.\n\n"
            "- Email: support@example.com\n"
            "- Telegram: https://t.me/benchatronics\n"
            "- Hours: 9:00–18:00 (Mon–Fri)"
        ),
    },
    "help": {
        "title": "Help",
        "body": (
            "Frequently asked questions:\n\n"
            "1) How do I reset my password?\n"
            "   Go to Settings → Change password or use ‘Forgotten password’ on the Sign In page.\n\n"
            "2) How do I change language?\n"
            "   Use the language switcher in the header; the site updates instantly."
        ),
    },
    "level": {
        "title": "Levels",
        "body": (
            "Level system rewards active users.\n\n"
            "- Level 1: New users\n"
            "- Level 2: Verified & active\n"
            "- Level 3+: Power users with extended perks"
        ),
    },
    "signin_reward": {
        "title": "Sign-in reward",
        "body": (
            "Sign in daily to collect rewards.\n\n"
            "Keep a streak to unlock bonus points and exclusive perks."
        ),
    },
}

WELCOME_ANNOUNCEMENT = {
    "title": "Welcome to Orbitpedia (metasearch) 🎉",
    "body": (
        "We’ve launched a refreshed Settings experience with language picker, "
        "profile management, and secure password tools. Thanks for trying it out!"
    ),
    "pinned": True,
    "is_published": True,
    "starts_at": None,  # or timezone.now()
    "ends_at": None,
}

class Command(BaseCommand):
    help = "Seed Info pages (About, Contact, Help, Level, Sign-in Reward) and a sample Announcement."

    def handle(self, *args, **options):
        created, updated = 0, 0

        # Info pages
        for key, data in PAGES.items():
            obj, was_created = InfoPage.objects.update_or_create(
                key=key,
                defaults={
                    "title": data["title"],
                    "body": data["body"],
                    "is_published": True,
                },
            )
            created += 1 if was_created else 0
            updated += 0 if was_created else 1
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if was_created else 'Updated'} InfoPage: {key}"
            ))

        # Sample announcement (create if not exists by title; update body/pinned if it does)
        ann, ann_created = Announcement.objects.update_or_create(
            title=WELCOME_ANNOUNCEMENT["title"],
            defaults={
                "body": WELCOME_ANNOUNCEMENT["body"],
                "pinned": WELCOME_ANNOUNCEMENT["pinned"],
                "is_published": WELCOME_ANNOUNCEMENT["is_published"],
                "starts_at": WELCOME_ANNOUNCEMENT["starts_at"],
                "ends_at": WELCOME_ANNOUNCEMENT["ends_at"],
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if ann_created else 'Updated'} Announcement: {ann.title}"
        ))

        self.stdout.write(self.style.NOTICE(
            f"Done. Info pages -> created: {created}, updated: {updated}."
        ))
