import os, time, json, re
import requests
from requests.exceptions import ReadTimeout, ConnectTimeout, SSLError, ConnectionError
from django.core.management.base import BaseCommand
from core.models import FuelStop
from openai import OpenAI  # pip install openai

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

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

USA_LAT_MIN, USA_LAT_MAX = 18.0, 72.0
USA_LON_MIN, USA_LON_MAX = -170.0, -50.0

def is_us_bounds(lat: float, lon: float) -> bool:
    return (USA_LAT_MIN <= lat <= USA_LAT_MAX) and (USA_LON_MIN <= lon <= USA_LON_MAX)

def clean_name(name: str) -> str:
    if not name:
        return ""
    n = name.strip()
    n = re.sub(r"\s*#\s*\d+\b", "", n)  # remove store numbers like #40514
    n = n.replace("’", "'")
    n = re.sub(r"\s+", " ", n).strip()
    return n

def places_search(text_query: str, api_key: str, timeout=(20, 60)):
    """
    Google Places Text Search (New)
    """
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.location,places.displayName,places.formattedAddress",
        "Content-Type": "application/json",
    }
    payload = {
        "textQuery": text_query,
        "regionCode": "US",
        "pageSize": 1
    }

    r = SESSION.post(PLACES_URL, headers=headers, json=payload, timeout=timeout)

    if r.status_code == 429:
        return "RATE_LIMIT", None
    if r.status_code == 403:
        return "FORBIDDEN", None
    if 500 <= r.status_code < 600:
        return "FAILED", None
    if 400 <= r.status_code < 500:
        return "FAILED", None

    data = r.json()
    places = data.get("places") or []
    if not places:
        return "NO_RESULT", None

    loc = (places[0].get("location") or {})
    try:
        lat = float(loc.get("latitude"))
        lon = float(loc.get("longitude"))
    except Exception:
        return "BAD_RESULT", None

    if not is_us_bounds(lat, lon):
        return "BAD_RESULT", None

    return "OK", (lat, lon)

def openai_suggest_queries(client: OpenAI, fs: FuelStop, max_q: int = 4):
    """
    OpenAI fallback: generate better Places queries only for failed rows
    """
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

    state_code = (fs.state or "").strip().upper()
    state_full = STATE_FULL.get(state_code, state_code)

    prompt = f"""
Return ONLY a JSON array of up to {max_q} short Google Places text queries (strings).
No explanations.

Goal: find the fuel station / travel center POI.

Data:
name: {fs.name}
address: {fs.address}
city: {fs.city}
state: {state_full}
country: USA

Make queries likely to match a gas station/truck stop POI.
"""

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": "Return only valid JSON array of strings."},
            {"role": "user", "content": prompt},
        ],
    )
    txt = (resp.output_text or "").strip()

    try:
        arr = json.loads(txt)
        if isinstance(arr, list):
            out = []
            for x in arr:
                if isinstance(x, str) and x.strip():
                    out.append(x.strip())
            return out[:max_q]
    except Exception:
        pass

    return []

class Command(BaseCommand):
    help = "Geocode missing FuelStops using Google Places (New) + OpenAI fallback queries"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--sleep", type=float, default=0.25)
        parser.add_argument("--max-retries", type=int, default=6)
        parser.add_argument("--openai-max-queries", type=int, default=4)

    def handle(self, *args, **opts):
        places_key = os.environ.get("GOOGLE_PLACES_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")

        if not places_key:
            self.stdout.write(self.style.ERROR("GOOGLE_PLACES_API_KEY env var not set"))
            return
        if not openai_key:
            self.stdout.write(self.style.ERROR("OPENAI_API_KEY env var not set"))
            return

        client = OpenAI(api_key=openai_key)

        limit = opts["limit"]
        sleep_s = opts["sleep"]
        max_retries = opts["max_retries"]
        oa_max_q = opts["openai_max_queries"]

        qs = FuelStop.objects.filter(lat__isnull=True, lon__isnull=True).order_by("id")[:limit]
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to geocode."))
            return

        ok = nores = rate = net = forb = 0

        for i, fs in enumerate(qs, start=1):
            state_code = (fs.state or "").strip().upper()
            state_full = STATE_FULL.get(state_code, state_code)
            name_clean = clean_name(fs.name)

            base_queries = [
                f"{name_clean}, {fs.city}, {state_full}, USA",
                f"{name_clean} truck stop, {fs.city}, {state_full}, USA",
                f"{name_clean} travel center, {fs.city}, {state_full}, USA",
                f"{fs.address}, {fs.city}, {state_full}, USA",
            ]

            coords = None

            # 1) Places base queries
            for q in base_queries:
                attempt = 0
                while attempt <= max_retries:
                    try:
                        status, got = places_search(q, places_key)
                        if status == "OK":
                            coords = got
                            break
                        if status == "RATE_LIMIT":
                            rate += 1
                            attempt += 1
                            time.sleep(1.2 * attempt)
                            continue
                        if status == "FORBIDDEN":
                            forb += 1
                            self.stdout.write(self.style.ERROR(
                                "403 from Places: enable Places API (New) + billing + correct key restrictions."
                            ))
                            return
                        break
                    except (ReadTimeout, ConnectTimeout, SSLError, ConnectionError):
                        net += 1
                        attempt += 1
                        time.sleep(1.2 * attempt)
                        continue
                if coords:
                    break

            # 2) OpenAI fallback only if missing
            if not coords:
                oa_queries = openai_suggest_queries(client, fs, max_q=oa_max_q)
                for q in oa_queries:
                    attempt = 0
                    while attempt <= max_retries:
                        try:
                            status, got = places_search(q, places_key)
                            if status == "OK":
                                coords = got
                                break
                            if status == "RATE_LIMIT":
                                rate += 1
                                attempt += 1
                                time.sleep(1.2 * attempt)
                                continue
                            if status == "FORBIDDEN":
                                forb += 1
                                self.stdout.write(self.style.ERROR(
                                    "403 from Places: enable Places API (New) + billing + correct key restrictions."
                                ))
                                return
                            break
                        except (ReadTimeout, ConnectTimeout, SSLError, ConnectionError):
                            net += 1
                            attempt += 1
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
                    f"Progress {i}/{total} | ok={ok} | no_result={nores} | rate_hits={rate} | net_errs={net}"
                )

            time.sleep(sleep_s)

        self.stdout.write(self.style.SUCCESS(
            f"Done. ok={ok}/{total}, no_result={nores}, rate_hits={rate}, net_errs={net}, forbidden={forb}"
        ))