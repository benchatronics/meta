import datetime
import random
import string

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import transaction

from main.models import Country, Hotel, Favorite

User = get_user_model()


class Command(BaseCommand):
    help = "Seed the database with demo countries, hotels (59 total), and random favorites"

    def _rand_name(self, prefix="Hotel"):
        """Generate a simple unique-ish name suffix like 'Aurora-7Q'."""
        letters = "".join(random.choices(string.ascii_uppercase, k=2))
        return f"{prefix}-{random.randint(3, 999)}{letters}"

    def _label_from_score(self, s: float) -> str:
        if s >= 4.5:
            return "perfect"
        if s >= 4.0:
            return "good"
        return "medium"

    def _random_available_date(self):
        # pick a date within ±20 days of today for nicer filtering demos
        delta = random.randint(-20, 20)
        return (timezone.now() + datetime.timedelta(days=delta)).date()

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed()  # allow natural randomness each run

        # Clear old data (favorites first due to FK constraints)
        Favorite.objects.all().delete()
        Hotel.objects.all().delete()
        Country.objects.all().delete()

        # Countries + Cities (expandable)
        country_cities = {
            ("🇩🇪", "Germany", "DE"):  ["Berlin", "Munich", "Hamburg", "Cologne"],
            ("🇨🇳", "China", "CN"):    ["Shanghai", "Beijing", "Shenzhen", "Guangzhou"],
            ("🇴🇲", "Oman", "OM"):     ["Muscat", "Salalah", "Sohar"],
            ("🇷🇺", "Russia", "RU"):   ["Moscow", "Saint Petersburg", "Kazan"],
            ("🇫🇷", "France", "FR"):   ["Paris", "Lyon", "Nice", "Marseille"],
            ("🇯🇵", "Japan", "JP"):    ["Tokyo", "Osaka", "Kyoto", "Nagoya"],
            # add a few more to diversify the 59
            ("🇺🇸", "United States", "US"): ["New York", "Los Angeles", "Chicago", "Miami", "San Francisco"],
            ("🇬🇧", "United Kingdom", "GB"): ["London", "Manchester", "Edinburgh", "Birmingham"],
            ("🇦🇪", "United Arab Emirates", "AE"): ["Dubai", "Abu Dhabi", "Sharjah"],
            ("🇪🇬", "Egypt", "EG"):   ["Cairo", "Alexandria", "Giza"],
        }

        # Create countries
        countries = {}
        for (flag, name, iso), _cities in country_cities.items():
            countries[name] = Country.objects.create(flag=flag, name=name, iso=iso)

        # A small image pool (Unsplash hotel-like images)
        image_pool = [
            "https://images.unsplash.com/photo-1566073771259-6a8506099945?q=80&w=1200&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?q=80&w=1200&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1496412705862-e0088f16f791?q=80&w=1200&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1505691938895-1758d7feb511?q=80&w=1200&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1536376072261-38c75010e6c9?q=80&w=1200&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1528909514045-2fa4ac7a08ba?q=80&w=1200&auto=format&fit=crop",
        ]

        hotels = []

        # Handful of curated hotels (with cities) to mirror your original six
        curated = [
            ("Arvane-Height", "Beautiful modern wide fenced, suitable for wifis.", "Germany", "Berlin", 4.1, True, 120, image_pool[0]),
            ("Golden Palace", "Luxury stay in the heart of the city.",            "China",   "Shanghai", 4.8, True, 200, image_pool[1]),
            ("Desert Pearl",  "Serene desert getaway with modern amenities.",     "Oman",    "Muscat",   4.5, False,150, image_pool[2]),
            ("Winter Crown",  "Cozy escape with snowy mountain views.",           "Russia",  "Moscow",   3.9, False, 80, image_pool[3]),
            ("Eiffel Luxe",   "Elegant hotel steps from the Eiffel Tower.",       "France",  "Paris",    4.7, True, 300, image_pool[4]),
            ("Tokyo Sky",     "Minimalist comfort with skyline views.",           "Japan",   "Tokyo",    4.3, False, 95, image_pool[5]),
        ]

        for name, desc, country_name, city, score, rec, pop, img in curated:
            hotels.append(
                Hotel.objects.create(
                    name=name,
                    description_short=desc,
                    country=countries[country_name],
                    city=city,
                    score=score,
                    label=self._label_from_score(score),
                    is_recommended=rec,
                    popularity=pop,
                    cover_image_url=img,
                    available_date=self._random_available_date(),
                    is_published=True,
                )
            )

        # Procedurally generate the rest up to exactly 59
        target_total = 59
        while len(hotels) < target_total:
            # pick a random country + city
            (flag, cname, iso), cities = random.choice(list(country_cities.items()))
            city = random.choice(cities)

            name = self._rand_name(prefix=random.choice(["Aurora", "Grand", "Palm", "Vista", "Crescent", "Marina", "Oasis", "Lagoon"]))
            desc = random.choice([
                "Contemporary rooms with fast Wi-Fi and city views.",
                "Beachfront escape with rooftop pool and spa.",
                "Business-friendly stay near transport hubs.",
                "Boutique charm with artisanal breakfast.",
                "Family suites with play area and courtyard.",
                "Skyline lounge and late-night room service.",
            ])

            score = round(random.uniform(3.2, 4.9), 1)
            label = self._label_from_score(score)
            is_rec = random.random() < 0.4  # ~40% recommended
            popularity = random.randint(50, 420)
            img = random.choice(image_pool)

            hotels.append(
                Hotel.objects.create(
                    name=name,
                    description_short=desc,
                    country=countries[cname],
                    city=city,
                    score=score,
                    label=label,
                    is_recommended=is_rec,
                    popularity=popularity,
                    cover_image_url=img,
                    available_date=self._random_available_date(),
                    is_published=True,
                )
            )

        # Ensure at least 3 demo users
        if not User.objects.exists():
            user1 = User.objects.create_user(phone="9001", password="pass1234")
            user2 = User.objects.create_user(phone="9002", password="pass1234")
            user3 = User.objects.create_user(phone="9003", password="pass1234")
        else:
            users = list(User.objects.all()[:3])
            while len(users) < 3:
                users.append(User.objects.create_user(phone=f"900{len(users)+1}", password="pass1234"))
            user1, user2, user3 = users[:3]

        all_users = [user1, user2, user3]

        # Random favorites
        for hotel in hotels:
            chosen_users = random.sample(all_users, k=random.randint(0, len(all_users)))
            for u in chosen_users:
                Favorite.objects.get_or_create(user=u, hotel=hotel)

        self.stdout.write(self.style.SUCCESS(f"✅ Seeded {len(hotels)} hotels with cities + random favorites."))
