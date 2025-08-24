from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from main.models import TaskTemplate, TaskPhase


class Command(BaseCommand):
    help = "Seed 25 TaskTemplate records (10 TRIAL, 10 NORMAL, 5 VIP). Idempotent by order_code."

    def handle(self, *args, **options):
        today = timezone.now().date()

        # -------- Helpers --------
        def row_common(phase, name, country, city, seed, order_code, worth_cents, commission_cents,
                       score: Decimal, label, is_published=True, is_recommended=False, popularity=500):
            return {
                "phase": phase,
                "name": name,
                "country": country,
                "city": city,
                "cover_image_url": f"https://picsum.photos/seed/{seed}/800/480",
                "order_code": order_code,
                "order_date": today,
                "worth_cents": worth_cents,
                "commission_cents": commission_cents,
                "score": score,
                "label": label,
                "is_published": is_published,
                "is_recommended": is_recommended,
                "popularity": popularity,
            }

        # Cycle some nice countries/cities for variety (no FK in your model)
        trial_places = [
            ("Germany", "Berlin"), ("Netherlands", "Amsterdam"), ("Spain", "Madrid"),
            ("Belgium", "Brussels"), ("Italy", "Rome"), ("France", "Lyon"),
            ("Portugal", "Porto"), ("Austria", "Vienna"), ("Sweden", "Stockholm"),
            ("Denmark", "Copenhagen"),
        ]
        normal_places = [
            ("Italy", "Milan"), ("France", "Paris"), ("Portugal", "Lisbon"),
            ("Ireland", "Dublin"), ("Norway", "Oslo"), ("Finland", "Helsinki"),
            ("Poland", "Warsaw"), ("Czechia", "Prague"), ("Greece", "Athens"),
            ("Hungary", "Budapest"),
        ]
        vip_places = [
            ("Germany", "Munich"), ("United Kingdom", "London"), ("Netherlands", "Rotterdam"),
            ("Switzerland", "Zurich"), ("Spain", "Barcelona"),
        ]

        # Label cycle
        labels = [
            TaskTemplate.Label.PERFECT,
            TaskTemplate.Label.GOOD,
            TaskTemplate.Label.MEDIUM,
            TaskTemplate.Label.GOOD,
            TaskTemplate.Label.PERFECT,
        ]

        rows = []

        # -------------------
        # TRIAL × 10 (worth = 0; commission here is just UI; payout follows SystemSettings)
        # -------------------
        for i in range(1, 11):
            country, city = trial_places[i - 1]
            rows.append(
                row_common(
                    phase=TaskPhase.TRIAL,
                    name=f"Trial Task {i}",
                    country=country,
                    city=city,
                    seed=f"trial-{i}",
                    order_code=f"TRIAL-{i:04d}",
                    worth_cents=0,
                    commission_cents=145,                         # e.g. €1.45
                    score=Decimal(str(4.5 + (i % 4) * 0.1)),      # 4.5–4.8
                    label=labels[i % len(labels)],
                    is_published=True,
                    is_recommended=(i in (1, 2, 3)),
                    popularity=1000 - i * 10,
                )
            )

        # -------------------
        # NORMAL × 10 (worth = 0 by your rule unless SystemSettings overrides in runtime)
        # -------------------
        for i in range(1, 11):
            country, city = normal_places[i - 1]
            rows.append(
                row_common(
                    phase=TaskPhase.NORMAL,
                    name=f"Normal Task {i}",
                    country=country,
                    city=city,
                    seed=f"normal-{i}",
                    order_code=f"NORM-{i:04d}",
                    worth_cents=0,
                    commission_cents=250,                         # e.g. €2.50 (UI)
                    score=Decimal(str(4.3 + (i % 5) * 0.1)),      # 4.3–4.7
                    label=labels[(i + 1) % len(labels)],
                    is_published=True,
                    is_recommended=(i in (1, 4, 7)),
                    popularity=900 - i * 9,
                )
            )

        # -------------------
        # VIP × 5 (now with actual worth)
        # -------------------
        vip_worths = [20000, 50000, 10000, 35000, 80000]  # €200, €500, €100, €350, €800
        vip_commissions = [2500, 4000, 1200, 3000, 6000]  # €25, €40, €12, €30, €60

        for i in range(1, 6):
            country, city = vip_places[i - 1]
            rows.append(
                row_common(
                    phase=TaskPhase.VIP,
                    name=f"VIP Task {i}",
                    country=country,
                    city=city,
                    seed=f"vip-{i}",
                    order_code=f"VIP-{i:04d}",
                    worth_cents=vip_worths[i - 1],
                    commission_cents=vip_commissions[i - 1],
                    score=Decimal(str(4.7 + (i % 3) * 0.1)),      # 4.7–4.9
                    label=labels[(i + 2) % len(labels)],
                    is_published=True,
                    is_recommended=(i in (1, 2)),
                    popularity=1200 - i * 20,
                )
            )

        # -------- Upsert (idempotent by order_code) --------
        created, updated = 0, 0
        for data in rows:
            obj, was_created = TaskTemplate.objects.get_or_create(
                order_code=data["order_code"],
                defaults=data,
            )
            if was_created:
                created += 1
            else:
                # Keep seed fresh on re-run
                for field in [
                    "phase", "name", "country", "city", "cover_image_url",
                    "order_date", "worth_cents", "commission_cents",
                    "score", "label", "is_published", "is_recommended", "popularity",
                ]:
                    setattr(obj, field, data[field])
                obj.save()
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"TaskTemplate seed complete — created: {created}, updated: {updated} (total intended: 25)"
        ))
