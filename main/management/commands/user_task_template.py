from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from main.models import UserTaskTemplate


class Command(BaseCommand):
    help = "Seed 20 UserTaskTemplate records with price=0 and commission=1.45"

    def handle(self, *args, **options):
        tasks = [
            # hotel_name, slug, country, city, score, label
            ("Eko Signature Hotel", "eko-signature-hotel", "Nigeria", "Lagos", "4.8", "PERFECT"),
            ("Transcorp Hilton", "transcorp-hilton", "Nigeria", "Abuja", "4.7", "PERFECT"),
            ("La Palm Royal Beach", "la-palm-royal-beach", "Ghana", "Accra", "4.5", "GOOD"),
            ("Serena Hotel", "serena-hotel", "Kenya", "Nairobi", "4.6", "PERFECT"),
            ("Radisson Blu", "radisson-blu-dubai", "UAE", "Dubai", "4.9", "PERFECT"),
            ("Kempinski Hotel", "kempinski-hotel-accra", "Ghana", "Accra", "4.8", "PERFECT"),
            ("Protea Hotel", "protea-hotel-ikeja", "Nigeria", "Lagos", "4.2", "GOOD"),
            ("The Wheatbaker", "the-wheatbaker", "Nigeria", "Lagos", "4.6", "PERFECT"),
            ("Golden Tulip", "golden-tulip-kumasi", "Ghana", "Kumasi", "4.3", "GOOD"),
            ("Fairmont Nile City", "fairmont-nile-city", "Egypt", "Cairo", "4.7", "PERFECT"),
            ("Sheraton Addis", "sheraton-addis", "Ethiopia", "Addis Ababa", "4.5", "GOOD"),
            ("Table Bay Hotel", "table-bay-hotel", "South Africa", "Cape Town", "4.8", "PERFECT"),
            ("Hotel Ibis", "hotel-ibis-cotonou", "Benin", "Cotonou", "4.0", "MEDIUM"),
            ("Novotel Dakar", "novotel-dakar", "Senegal", "Dakar", "4.2", "GOOD"),
            ("Royal Senchi", "royal-senchi", "Ghana", "Akosombo", "4.6", "PERFECT"),
            ("InterContinental Lusaka", "intercontinental-lusaka", "Zambia", "Lusaka", "4.4", "GOOD"),
            ("Hilton Alexandria", "hilton-alexandria", "Egypt", "Alexandria", "4.5", "GOOD"),
            ("Mövenpick Ambassador", "movenpick-ambassador", "Ghana", "Accra", "4.7", "PERFECT"),
            ("Four Points by Sheraton", "four-points-lagos", "Nigeria", "Lagos", "4.5", "GOOD"),
            ("Southern Sun", "southern-sun-maputo", "Mozambique", "Maputo", "4.3", "GOOD"),
        ]

        created_count = 0
        for hotel_name, slug, country, city, score, label in tasks:
            obj, created = UserTaskTemplate.objects.get_or_create(
                slug=slug,
                defaults=dict(
                    hotel_name=hotel_name,
                    country=country,
                    city=city,
                    cover_image_url=f"https://picsum.photos/seed/{slug}/600/400",
                    task_price=Decimal("0.00"),
                    task_commission=Decimal("1.45"),
                    task_score=Decimal(score),
                    task_label=label,
                    is_admin_task=False,
                    status=UserTaskTemplate.Status.ACTIVE,
                    task_date=timezone.now().date(),
                )
            )
            if created:
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f"✅ Seeded {created_count} UserTaskTemplate records."))
