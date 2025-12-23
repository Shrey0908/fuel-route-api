import os
import time
import requests
from requests.exceptions import ReadTimeout, ConnectTimeout, SSLError, ConnectionError
from django.core.management.base import BaseCommand
from core.models import FuelStop

# docs: /search endpoint, api_key via query param or Authorization: Bearer header
# https://geocode.maps.co/docs/endpoints/  (ref only; no need in code)
BASE_URLS = [
    "https://geocode.maps.co/search",
    "https://geocode.maps.co/search/",
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "fuel-route-api/1.0",
    "Accept": "application/json",
})

USA_LAT_MIN, USA_LAT_MAX = 18.0, 72.0
USA_LON_MIN, USA_LON_MAX = -170.0, -50.0

def is_us_bounds(lat: float, lon: float) -> bool:
    return (USA_LAT_MIN <= lat <= USA_LAT_MAX) and (USA_LON_MIN <= lon <= USA_LON_MAX)

def call_mapsco(q: str, api_key: str, timeout=(20, 60), us_only=True):
    """
    Returns (status, (lat, lon) or None)

    status:
      OK, NO_RESULT, RATE_LIMIT, FORBIDDEN, FAILED, BAD_RESULT
    """
    params_base = {
        "q": q,
        "api_key": api_key,
        "format": "json",          # docs supported
        "limit": 1,                # docs: 0-40
        "addressdetails": 0,       # default 1 -> make response lighter
        "extratags": 0,            # default 1 -> lighter
        "namedetails": 0,          # default 1 -> lighter
        "dedupe": 1,               # default 1
    }
    if us_only:
        # docs: countrycodes parameter exists
        params_base["countrycodes"] = "us"

    headers = {"Authorization": f"Bearer {api_key}"}

    for base in BASE_URLS:
        try:
            r = SESSION.get(base, params=params_base, headers=headers, timeout=timeout)

            if r.status_code == 429:
                return "RATE_LIMIT", None
            if r.status_code == 403:
                return "FORBIDDEN", None

            # Some queries sometimes return 404; don't crash
            if r.status_code == 404:
                continue

            # 5xx: transient server issues
            if 500 <= r.status_code < 600:
                return "FAILED", None

            # For other 4xx (e.g., 400), treat as failure but don't crash
            if 400 <= r.status_code < 500:
                return "FAILED", None

            data = r.json()

            # Sometimes response could be dict (PS shows objects); normalize to list
            if isinstance(data, dict):
                data = [data]

            if not data:
                return "NO_RESULT", None

            try:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
            except Exception:
                return "BAD_RESULT", None

            # extra safety: enforce USA bounds even if countrycodes fails
            if us_only and not is_us_bounds(lat, lon):
                return "BAD_RESULT", None

            return "OK", (lat, lon)

        except (ReadTimeout, ConnectTimeout, SSLError, ConnectionError):
            # Try next base url or retry at higher level
            continue
        except Exception:
            continue

    return "FAILED", None


class Command(BaseCommand):
    help = "Geocode missing FuelStops using geocode.maps.co (robust retries, US-only, lighter payload)"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=300)         # batch size
        parser.add_argument("--sleep", type=float, default=0.9)       # slower = more stable
        parser.add_argument("--max-retries", type=int, default=6)
        parser.add_argument("--us-only", action="store_true", default=True)
        parser.add_argument("--no-us-only", action="store_false", dest="us_only")

    def handle(self, *args, **opts):
        api_key = os.environ.get("GEOCODE_MAPS_API_KEY")
        if not api_key:
            self.stdout.write(self.style.ERROR("GEOCODE_MAPS_API_KEY env var not set"))
            return

        limit = opts["limit"]
        sleep_s = opts["sleep"]
        max_retries = opts["max_retries"]
        us_only = opts["us_only"]

        qs = FuelStop.objects.filter(lat__isnull=True, lon__isnull=True).order_by("id")[:limit]
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to geocode (all rows have lat/lon)."))
            return

        ok = 0
        nores = 0
        rate_hits = 0
        net_errs = 0
        failed = 0

        for i, fs in enumerate(qs, start=1):
            # Truckstop dataset me "Address" highway-exit style hota hai,
            # so name+city+state is usually strongest.
            queries = [
                f"{fs.name}, {fs.city}, {fs.state}, USA",
                f"{fs.name} travel center, {fs.city}, {fs.state}, USA",
                f"{fs.name} truck stop, {fs.city}, {fs.state}, USA",
                f"{fs.address}, {fs.city}, {fs.state}, USA",
            ]

            coords = None

            for q in queries:
                attempt = 0
                while attempt <= max_retries:
                    try:
                        status, got = call_mapsco(q, api_key, us_only=us_only)

                        if status == "OK":
                            coords = got
                            break

                        if status == "RATE_LIMIT":
                            rate_hits += 1
                            time.sleep(2.0 + attempt)  # backoff
                            attempt += 1
                            continue

                        if status == "FORBIDDEN":
                            self.stdout.write(self.style.ERROR("403 Forbidden: API key issue / blocked."))
                            return

                        # NO_RESULT / BAD_RESULT / FAILED -> don't keep retrying same query too much
                        if status in ("NO_RESULT", "BAD_RESULT", "FAILED"):
                            break

                        break

                    except (ReadTimeout, ConnectTimeout, SSLError, ConnectionError):
                        attempt += 1
                        net_errs += 1
                        time.sleep(1.2 * attempt)  # increasing backoff
                        continue

                if coords:
                    break

            if coords:
                lat, lon = coords
                FuelStop.objects.filter(id=fs.id).update(lat=lat, lon=lon)
                ok += 1
            else:
                # classify: if all queries failed, count as nores (practically)
                nores += 1

            if i % 50 == 0:
                self.stdout.write(
                    f"Progress {i}/{total} | ok={ok} | no_result={nores} | rate_hits={rate_hits} | net_errs={net_errs} | failed={failed}"
                )

            time.sleep(sleep_s)

        self.stdout.write(self.style.SUCCESS(
            f"Done. ok={ok}/{total}, no_result={nores}, rate_hits={rate_hits}, net_errs={net_errs}"
        ))
