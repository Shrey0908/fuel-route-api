import requests
from django.core.cache import cache

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "fuel-route-api/1.0",
    "Accept": "application/json",
})

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

USA_LAT_MIN, USA_LAT_MAX = 18.0, 72.0
USA_LON_MIN, USA_LON_MAX = -170.0, -50.0

def geocode_us(text: str):
    key = f"nominatim:{text.strip().lower()}"
    cached = cache.get(key)
    if cached:
        return cached

    params = {
        "q": text,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "us",
        "addressdetails": 1,
    }
    r = SESSION.get(NOMINATIM_URL, params=params, timeout=(15, 60))
    if r.status_code == 429:
        return None
    r.raise_for_status()

    data = r.json()
    if not data:
        return None

    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    if not (USA_LAT_MIN <= lat <= USA_LAT_MAX and USA_LON_MIN <= lon <= USA_LON_MAX):
        return None

    name = data[0].get("display_name") or text
    out = (lat, lon, name)
    cache.set(key, out, timeout=60 * 60 * 24 * 7)
    return out
