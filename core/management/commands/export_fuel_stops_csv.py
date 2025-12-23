import csv
from django.core.management.base import BaseCommand
from core.models import FuelStop  # <-- yahan apna actual model name set karo

class Command(BaseCommand):
    help = "Export fuel stops to CSV (includes lat/lon)"

    def add_arguments(self, parser):
        parser.add_argument("--out", default="fuel-stops-with-latlon.csv")

    def handle(self, *args, **opts):
        out = opts["out"]
        qs = FuelStop.objects.all().values(
            "id", "name", "address", "city", "state",
            "lat", "lon", "price", "opis_id", "rack_id",
            "created_at", "updated_at",
        )

        rows = list(qs)
        if not rows:
            self.stdout.write(self.style.WARNING("No rows found."))
            return

        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        self.stdout.write(self.style.SUCCESS(f"✅ Exported {len(rows)} rows to {out}"))
