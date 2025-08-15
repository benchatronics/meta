import random
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from main.models import Country, Hotel, Favorite

User = get_user_model()

class Command(BaseCommand):
    help = "Seed the database with demo hotels for dashboard"

    def handle(self, *args, **options):
        # Clear old data
        Favorite.objects.all().delete()
        Hotel.objects.all().delete()
        Country.objects.all().delete()

        # Countries
        countries_data = [
            ("🇩🇪", "Germany", "DE"),
            ("🇨🇳", "China", "CN"),
            ("🇴🇲", "Oman", "OM"),
            ("🇷🇺", "Russia", "RU"),
            ("🇫🇷", "France", "FR"),
            ("🇯🇵", "Japan", "JP"),
        ]
        countries = {}
        for flag, name, iso in countries_data:
            countries[name] = Country.objects.create(flag=flag, name=name, iso=iso)

        # Hotels
        hotels_data = [
            ("Arvane-Height", "Beautiful modern wide fenced, suitable for wifis.", "Germany", 4.1, "good", True, 120,
             "https://images.unsplash.com/photo-1536376072261-38c75010e6c9?q=80&w=1200&auto=format&fit=crop"),
            ("Golden Palace", "Luxury stay in the heart of the city.", "China", 4.8, "perfect", True, 200,
             "https://images.unsplash.com/photo-1566073771259-6a8506099945?q=80&w=1200&auto=format&fit=crop"),
            ("Desert Pearl", "Serene desert getaway with modern amenities.", "Oman", 4.5, "perfect", False, 150,
             "https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?q=80&w=1200&auto=format&fit=crop"),
            ("Winter Crown", "Cozy escape with snowy mountain views.", "Russia", 3.9, "medium", False, 80,
             "https://images.unsplash.com/photo-1505691938895-1758d7feb511?q=80&w=1200&auto=format&fit=crop"),
            ("Eiffel Luxe", "Elegant hotel steps from the Eiffel Tower.", "France", 4.7, "perfect", True, 300,
             "https://images.unsplash.com/photo-1505691938895-1758d7feb511?q=80&w=1200&auto=format&fit=crop"),
            ("Tokyo Sky", "Minimalist comfort with skyline views.", "Japan", 4.3, "good", False, 95,
             "https://images.unsplash.com/photo-1496412705862-e0088f16f791?q=80&w=1200&auto=format&fit=crop"),
        ]

        hotels = []
        for name, desc, country_name, score, label, rec, pop, img in hotels_data:
            hotel = Hotel.objects.create(
                name=name,
                description_short=desc,
                country=countries[country_name],
                score=score,
                label=label,
                is_recommended=rec,
                popularity=pop,
                cover_image_url=img,
                available_date=timezone.now().date(),
                is_published=True
            )
            hotels.append(hotel)

        # Create demo users if none exist
        if not User.objects.exists():
            user1 = User.objects.create_user(phone="9001", password="pass1234")
            user2 = User.objects.create_user(phone="9002", password="pass1234")
            user3 = User.objects.create_user(phone="9003", password="pass1234")
        else:
            users = list(User.objects.all())
            while len(users) < 3:
                users.append(User.objects.create_user(phone=f"900{len(users)+1}", password="pass1234"))
            user1, user2, user3 = users[:3]

        all_users = [user1, user2, user3]

        # Add random favorites
        for hotel in hotels:
            chosen_users = random.sample(all_users, k=random.randint(0, len(all_users)))
            for u in chosen_users:
                Favorite.objects.create(user=u, hotel=hotel)

        self.stdout.write(self.style.SUCCESS("✅ Seeded hotels with random favorites."))