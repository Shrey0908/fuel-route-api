import csv
import io
import requests
from django.core.management.base import BaseCommand
from core.models import FuelStop

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"

def parse_lon_lat(parts):
    """
    Census response me lon,lat generally "x,y" format me hota hai.
    Hum safest parse karte hain.
    """
    for p in parts:
        p = p.strip()
        if "," in p:
            a, b = p.split(",", 1)
            try:
                lon = float(a)
                lat = float(b)
                # USA-ish sanity
                if -180 <= lon <= 0 and 0 <= lat <= 90:
                    return lon, lat
            except:
                pass
    return None, None

class Command(BaseCommand):
    help = "Batch geocode FuelStops via US Census and update lat/lon"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10000)

    def handle(self, *args, **opts):
        limit = opts["limit"]

        qs = FuelStop.objects.filter(lat__isnull=True, lon__isnull=True).order_by("id")[:limit]
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to geocode (all rows already have lat/lon)."))
            return

        # Build batch CSV: id, street, city, state, zip
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for fs in qs:
            w.writerow([fs.id, fs.address, fs.city, fs.state, ""])

        files = {"addressFile": ("addressbatch.csv", buf.getvalue(), "text/csv")}
        data = {"benchmark": "Public_AR_Current"}

        self.stdout.write(f"Submitting {total} addresses to Census batch geocoder...")
        r = requests.post(BATCH_URL, files=files, data=data, timeout=180)
        r.raise_for_status()

        lines = r.text.splitlines()
        updated = 0
        unmatched = 0

        for line in lines:
            parts = list(csv.reader([line]))[0]
            if not parts:
                continue

            rec_id = parts[0].strip()
            if not rec_id.isdigit():
                continue
            fs_id = int(rec_id)

            match_status = parts[2].strip().lower() if len(parts) > 2 else ""
            lon, lat = parse_lon_lat(parts)

            if match_status.startswith("match") and lon is not None and lat is not None:
                FuelStop.objects.filter(id=fs_id).update(lat=lat, lon=lon)
                updated += 1
            else:
                unmatched += 1

        self.stdout.write(self.style.SUCCESS(f"Geocode done. updated={updated}/{total}, unmatched={unmatched}"))
