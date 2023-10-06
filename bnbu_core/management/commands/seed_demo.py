"""
Populate an empty database with a realistic working set.

The Docker stack runs this on boot so the first thing anyone sees is a product
with data in it: priced properties including the awkward ones, a reviewed
lease with clause findings, and regulation answers for several cities. It is
idempotent - running it twice changes nothing.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from bnbu_core.llm import Purpose
from bnbu_core.llm.demo_provider import DemoProvider
from bnbu_core.regulation_sources import RegulationSourceChain
from lease.analysis import parse_completion
from lease.models import Document, Lease
from regulations import services as regulation_services
from regulations.models import Regulations
from rental.models import RentalProperty

CustomUser = get_user_model()

DEMO_PASSWORD = "DemoPass!123"

USERS = [
    ("admin@bnbu.test", "admin", True),
    ("analyst@bnbu.test", "client", False),
    # A second client who owns nothing, so the ownership scoping is visible
    # rather than merely asserted: signing in as this account shows empty
    # lists, and the analyst's rows answer 404.
    ("rival@bnbu.test", "client", False),
]

PROPERTIES = [
    # location, rent, beds, baths, sqft, annual revenue estimate
    ("412 Cypress Ave, Austin, TX", 2100, 2, 2, 1180, 68000),
    ("9 Cedar Ln, Nashville, TN", 3100, 3, 2, 1640, 41000),
    ("77 Harbor St, Scottsdale, AZ", 2450, 2, 2, 1320, 74000),
    ("18 Birch Ct, Kirkland, WA", 3600, 4, 3, 2100, 121000),
    ("201 Grand Blvd, Nashville, TN", 1750, 1, 1, 760, 39000),
    ("6 Willow Way, Austin, TX", 2900, 3, 2, 1580, None),
    ("55 Palm Dr, Scottsdale, AZ", 2050, 2, 1, 1040, None),
    ("130 Union St, Kirkland, WA", 4100, 4, 3, 2350, 96000),
]

LEASES = [
    ("18 Birch Ct", "Kirkland", "WA", "98033", "birch-ct-lease.pdf", True),
    ("412 Cypress Ave", "Austin", "TX", "78702", "cypress-ave-lease.pdf", True),
    ("9 Cedar Ln", "Nashville", "TN", "37206", "cedar-ln-lease.pdf", False),
]

SEARCHES = ["Kirkland, WA", "Austin, TX", "New York, NY", "Scottsdale, AZ"]


class Command(BaseCommand):
    help = "Create demo users, priced properties, reviewed leases and regulation answers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo data before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260923)

        if options["reset"]:
            self._reset()

        users = self._users()
        analyst = users["analyst@bnbu.test"]

        created = {
            "properties": self._properties(analyst),
            "leases": self._leases(analyst),
            "regulations": self._regulations(analyst),
        }

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded {properties} properties, {leases} leases and {regulations} "
                "regulation searches.".format(**created)
            )
        )
        self.stdout.write(f"Sign in as admin@bnbu.test / {DEMO_PASSWORD}")

    # -- pieces ------------------------------------------------------------

    def _reset(self):
        emails = [email for email, _, _ in USERS]
        RentalProperty.objects.filter(
            user_id__in=CustomUser.objects.filter(email__in=emails).values_list("id", flat=True)
        ).delete()
        Lease.objects.filter(user__email__in=emails).delete()
        Regulations.objects.filter(user__email__in=emails).delete()
        CustomUser.objects.filter(email__in=emails).delete()

    def _users(self):
        users = {}
        for email, user_type, is_staff in USERS:
            user, created = CustomUser.objects.get_or_create(
                email=email,
                defaults={
                    "user_type": user_type,
                    "is_staff": is_staff,
                    "is_superuser": is_staff,
                    "is_active": True,
                    "is_first_login": False,
                },
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
            users[email] = user
        return users

    def _properties(self, user):
        if RentalProperty.objects.filter(user_id=user.id).exists():
            return RentalProperty.objects.filter(user_id=user.id).count()

        from bnbu_constants.utility import calculate_monthly_profit, determine_property_status

        created = 0
        for index, (location, rent, beds, baths, sqft, revenue) in enumerate(PROPERTIES):
            breakdown = calculate_monthly_profit(revenue, rent, beds)
            status = determine_property_status(beds, breakdown.monthly_estimated_profit)
            RentalProperty.objects.create(
                user_id=user.id,
                location=location,
                rent=rent,
                no_of_bedrooms=beds,
                no_of_bathrooms=baths,
                square_feet=sqft,
                property_zillow_link=f"https://www.zillow.com/homedetails/{index + 1000}_zpid/",
                adr=Decimal(str(round((revenue or 0) / 260, 2))) if revenue else None,
                occupancy_rate=(
                    Decimal(str(round(random.uniform(0.52, 0.78), 2))) if revenue else None
                ),
                utilities=(
                    Decimal(str(breakdown.utilities)) if breakdown.utilities is not None else None
                ),
                yearly_projected_revenue=revenue,
                yearly_rent_cost_util=(
                    Decimal(str(breakdown.yearly_rent_cost_util))
                    if breakdown.yearly_rent_cost_util is not None
                    else None
                ),
                monthly_estimated_profit=(
                    Decimal(str(breakdown.monthly_estimated_profit))
                    if breakdown.monthly_estimated_profit is not None
                    else None
                ),
                property_status=status,
                batch_id=1 if index < 5 else 2,
            )
            created += 1
        return created

    def _leases(self, user):
        if Lease.objects.filter(user=user).exists():
            return Lease.objects.filter(user=user).count()

        # The seeded review is produced by the demo provider and parsed by the
        # same code the real pipeline uses, so what is on screen is a genuine
        # analysis payload rather than a hand-written fixture.
        provider = DemoProvider()
        completion = provider.complete(
            [{"role": "user", "content": "seed"}], purpose=Purpose.LEASE_ANALYSIS
        )
        analysis = parse_completion(completion, pages=14)

        created = 0
        for offset, (address, city, state, zip_code, filename, reviewed) in enumerate(LEASES):
            lease = Lease.objects.create(
                user=user, address1=address, city=city, state=state, zip_code=zip_code
            )
            document = Document(
                lease=lease,
                name=filename,
                file_url=f"https://example.invalid/leases/{filename}",
            )
            if reviewed:
                document.status = analysis.verdict
                document.analysis = analysis.as_analysis_payload()
                document.gpt_response = analysis.as_gpt_response()
            document.save()

            if reviewed and offset == 0:
                document.chat_history = [
                    {
                        "role": "user",
                        "content": "Which clause should I push back on first?",
                        "timestamp": _ts(offset),
                    },
                    {
                        "role": "assistant",
                        "content": provider.complete(
                            [{"role": "user", "content": "seed"}], purpose=Purpose.LEASE_CHAT
                        ).text,
                        "timestamp": _ts(offset, minutes=1),
                    },
                ]
                document.save(update_fields=["chat_history"])
            created += 1
        return created

    def _regulations(self, user):
        if Regulations.objects.filter(user=user).exists():
            return Regulations.objects.filter(user=user).count()

        # The curated source answers the seeded cities locally, so seeding
        # works with no API key and no worker running.
        chain = RegulationSourceChain()
        created = 0
        for search in SEARCHES:
            regulation = Regulations.objects.create(user=user, search=search, status="pending")
            finding = chain.lookup(search)
            if finding is not None:
                regulation_services.apply_finding(regulation, finding)
            created += 1
        return created


def _ts(offset: int, minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset, minutes=-minutes)).isoformat()
