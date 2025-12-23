import os
import re
import time
import requests
from requests.exceptions import ReadTimeout, ConnectTimeout, SSLError, ConnectionError
from django.core.management.base import BaseCommand
from core.models import FuelStop

PLACES_TEXTSEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "fuel-route-api/1.0",
    "Content-Type": "application/json",
})

USA_LAT_MIN, USA_LAT_MAX = 18.0, 72.0
USA_LON_MIN, USA_LON_MAX = -170.0, -50.0

STATE_FULL = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California","CO":"Colorado","CT":"Connecticut",
    "DE":"Delaware","FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa",
    "KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts","MI":"Michigan",
    "MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire",
    "NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma",
    "OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee",
    "TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming",
    "DC":"District of Columbia"
}

def clean_name(name: str) -> str:
    if not name:
        return ""
    n = name.strip()
    n = re.sub(r"\s*#\s*\d+\b", "", n)  # remove store numbers like "#40514"
    n = n.replace("’", "'")
    n = re.sub(r"\s+", " ", n).strip()
    return n

def is_us_bounds(lat: float, lon: float) -> bool:
    return (USA_LAT_MIN <= lat <= USA_LAT_MAX) and (USA_LON_MIN <= lon <= USA_LON_MAX)

def places_text_search(query: str, api_key: str, timeout=(20, 60)):
    """
    Places Text Search (New): POST https://places.googleapis.com/v1/places:searchText
    Needs X-Goog-Api-Key and X-Goog-FieldMask.  :contentReference[oaicite:4]{index=4}
    """
    headers = {
        "X-Goog-Api-Key": api_key,
        # Keep it minimal for cost + speed: location + display name + types
        "X-Goog-FieldMask": "places.location,places.displayName,places.types",
    }

    payload = {
        "textQuery": query,
        "regionCode": "US",
        "includedType": "gas_station",        # filter by type :contentReference[oaicite:5]{index=5}
        "strictTypeFiltering": True,
        "pageSize": 1,
    }

    r = SESSION.post(PLACES_TEXTSEARCH_URL, headers=headers, json=payload, timeout=timeout)

    if r.status_code == 429:
        return "RATE_LIMIT", None
    if r.status_code == 403:
        return "FORBIDDEN", None
    if 500 <= r.status_code < 600:
        return "FAILED", None

    # 4xx also possible if fieldmask missing etc.
    if 400 <= r.status_code < 500:
        return "FAILED", None

    data = r.json()
    places = data.get("places") or []
    if not places:
        return "NO_RESULT", None

    loc = places[0].get("location") or {}
    try:
        lat = float(loc.get("latitude"))
        lon = float(loc.get("longitude"))
    except Exception:
        return "BAD_RESULT", None

    if not is_us_bounds(lat, lon):
        return "BAD_RESULT", None

    return "OK", (lat, lon)


class Command(BaseCommand):
    help = "Geocode FuelStops using Google Places Text Search (New) for high match rate"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--sleep", type=float, default=0.25)      # ~4 req/sec
        parser.add_argument("--max-retries", type=int, default=6)

    def handle(self, *args, **opts):
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            self.stdout.write(self.style.ERROR("GOOGLE_MAPS_API_KEY env var not set"))
            return

        limit = opts["limit"]
        sleep_s = opts["sleep"]
        max_retries = opts["max_retries"]

        qs = FuelStop.objects.filter(lat__isnull=True, lon__isnull=True).order_by("id")[:limit]
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to geocode."))
            return

        ok = 0
        nores = 0
        rate_hits = 0
        net_errs = 0

        for i, fs in enumerate(qs, start=1):
            state_code = (fs.state or "").strip().upper()
            state_full = STATE_FULL.get(state_code, state_code)
            name_clean = clean_name(fs.name)

            # Multiple query variants (POI style)
            queries = [
                f"{name_clean}, {fs.city}, {state_full}, USA",
                f"{name_clean} travel center, {fs.city}, {state_full}, USA",
                f"{name_clean} truck stop, {fs.city}, {state_full}, USA",
                f"{name_clean} gas station, {fs.city}, {state_full}, USA",
            ]

            coords = None

            for q in queries:
                attempt = 0
                while attempt <= max_retries:
                    try:
                        status, got = places_text_search(q, api_key)

                        if status == "OK":
                            coords = got
                            break

                        if status == "RATE_LIMIT":
                            rate_hits += 1
                            attempt += 1
                            time.sleep(1.5 * attempt)
                            continue

                        if status == "FORBIDDEN":
                            self.stdout.write(self.style.ERROR(
                                "403 Forbidden: API not enabled / billing not enabled / key restricted incorrectly."
                            ))
                            return

                        # NO_RESULT / BAD_RESULT / FAILED -> try next query variant
                        break

                    except (ReadTimeout, ConnectTimeout, SSLError, ConnectionError):
                        attempt += 1
                        net_errs += 1
                        time.sleep(1.2 * attempt)
                        continue

                if coords:
                    break

            if coords:
                lat, lon = coords
                FuelStop.objects.filter(id=fs.id).update(lat=lat, lon=lon)
                ok += 1
            else:
                nores += 1

            if i % 50 == 0:
                self.stdout.write(
                    f"Progress {i}/{total} | ok={ok} | no_result={nores} | rate_hits={rate_hits} | net_errs={net_errs}"
                )

            time.sleep(sleep_s)

        self.stdout.write(self.style.SUCCESS(
            f"Done. ok={ok}/{total}, no_result={nores}, rate_hits={rate_hits}, net_errs={net_errs}"
        ))
