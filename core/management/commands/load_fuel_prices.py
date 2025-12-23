import csv
from decimal import Decimal
from django.core.management.base import BaseCommand
from core.models import FuelStop

class Command(BaseCommand):
    help = "Load fuel prices CSV into FuelStop table"

    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, **opts):
        path = opts["csv_path"]
        created = 0
        updated = 0

        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                opis_id = int(row["OPIS Truckstop ID"])
                defaults = dict(
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=row["City"].strip(),
                    state=row["State"].strip(),
                    rack_id=int(row["Rack ID"]),
                    price=Decimal(str(row["Retail Price"])),
                )

                _, is_created = FuelStop.objects.update_or_create(
                    opis_id=opis_id,
                    address=defaults["address"],
                    city=defaults["city"],
                    state=defaults["state"],
                    defaults=defaults,
                )
                created += 1 if is_created else 0
                updated += 0 if is_created else 1

        self.stdout.write(self.style.SUCCESS(f"Done. created={created}, updated={updated}"))
