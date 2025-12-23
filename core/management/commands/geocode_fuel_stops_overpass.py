import os
import re
import time
import requests
from requests.exceptions import ReadTimeout, ConnectTimeout, SSLError, ConnectionError
from django.core.management.base import BaseCommand
from core.models import FuelStop

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "fuel-route-api/1.0",
    "Accept": "application/json",
})

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

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    s = s.replace("’", "'")
    s = re.sub(r"\s*#\s*\d+\b", "", s)      # remove store numbers
    s = re.sub(r"[^a-z0-9\s']", " ", s)     # keep simple chars
    s = re.sub(r"\s+", " ", s).strip()
    # normalize common words
    s = s.replace("travel center", "travelcentre")
    s = s.replace("travel center", "travelcentre")
    s = s.replace("truck stop", "truckstop")
    return s

def token_overlap_score(a: str, b: str) -> float:
    A = set(a.split())
    B = set(b.split())
    if not A or not B:
        return 0.0
    inter = len(A & B)
    return inter / max(len(A), len(B))

def overpass_fuel_pois(lat: float, lon: float, radius_m: int = 20000, timeout_s: int = 60):
    """
    Pull fuel POIs around a point (amenity=fuel).
    """
    query = f"""
    [out:json][timeout:{timeout_s}];
    (
      node["amenity"="fuel"](around:{radius_m},{lat},{lon});
      way["amenity"="fuel"](around:{radius_m},{lat},{lon});
      relation["amenity"="fuel"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """
    last_err = None
    for url in OVERPASS_URLS:
        try:
            r = SESSION.post(url, data=query.encode("utf-8"), timeout=(15, timeout_s))
            if r.status_code == 429:
                time.sleep(2.0)
                continue
            r.raise_for_status()
            return r.json().get("elements", [])
        except Exception as e:
            last_err = e
            continue
    raise last_err

def get_center(el):
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    c = el.get("center")
    if c:
        return float(c.get("lat")), float(c.get("lon"))
    return None

class Command(BaseCommand):
    help = "Geocode FuelStops by matching OSM fuel POIs via Overpass around city centroid (free)"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--sleep", type=float, default=1.0)
        parser.add_argument("--radius-m", type=int, default=25000)

    def handle(self, *args, **opts):
        limit = opts["limit"]
        sleep_s = opts["sleep"]
        radius_m = opts["radius_m"]

        qs = FuelStop.objects.filter(lat__isnull=True, lon__isnull=True).order_by("id")[:limit]
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to geocode."))
            return

        ok = 0
        nores = 0
        net_errs = 0

        # we need a seed point per city/state: use maps.co for city centroid (but you already have maps.co key)
        maps_key = os.environ.get("GEOCODE_MAPS_API_KEY")
        if not maps_key:
            self.stdout.write(self.style.ERROR("GEOCODE_MAPS_API_KEY not set (needed for city centroid)."))
            return

        # small helper: city centroid via maps.co
        def city_centroid(city, state):
            q = f"{city}, {STATE_FULL.get(state, state)}, USA"
            try:
                r = SESSION.get("https://geocode.maps.co/search", params={
                    "q": q,
                    "api_key": maps_key,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "us",
                    "addressdetails": 0,
                    "extratags": 0,
                    "namedetails": 0,
                }, timeout=(15, 60))
                if r.status_code in (403, 429, 500, 502, 503, 504):
                    return None
                data = r.json()
                if isinstance(data, dict):
                    data = [data]
                if not data:
                    return None
                return float(data[0]["lat"]), float(data[0]["lon"])
            except Exception:
                return None

        # cache centroids to avoid repeated calls
        centroid_cache = {}

        for i, fs in enumerate(qs, start=1):
            city = (fs.city or "").strip()
            state = (fs.state or "").strip().upper()

            key = (city.lower(), state)
            if key not in centroid_cache:
                centroid_cache[key] = city_centroid(city, state)

            centroid = centroid_cache.get(key)
            if not centroid:
                nores += 1
                continue

            try:
                elements = overpass_fuel_pois(centroid[0], centroid[1], radius_m=radius_m)
            except (ReadTimeout, ConnectTimeout, SSLError, ConnectionError):
                net_errs += 1
                continue
            except Exception:
                net_errs += 1
                continue

            target = clean_text(fs.name)
            best = None
            best_score = 0.0

            # match by name overlap
            for el in elements:
                tags = el.get("tags") or {}
                name = tags.get("name") or ""
                name_c = clean_text(name)
                if not name_c:
                    continue
                score = token_overlap_score(target, name_c)
                if score > best_score:
                    best_score = score
                    best = el

            if best and best_score >= 0.35:
                center = get_center(best)
                if center:
                    FuelStop.objects.filter(id=fs.id).update(lat=center[0], lon=center[1])
                    ok += 1
                else:
                    nores += 1
            else:
                nores += 1

            if i % 50 == 0:
                self.stdout.write(f"Progress {i}/{total} | ok={ok} | no_result={nores} | net_errs={net_errs}")

            time.sleep(sleep_s)

        self.stdout.write(self.style.SUCCESS(f"Done. ok={ok}/{total}, no_result={nores}, net_errs={net_errs}"))
