import requests
from django.core.cache import cache

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "fuel-route-api/1.0", "Accept": "application/json"})

PLACES_NEW_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

USA_LAT_MIN, USA_LAT_MAX = 18.0, 72.0
USA_LON_MIN, USA_LON_MAX = -170.0, -50.0

def _is_us(lat: float, lon: float) -> bool:
    return (USA_LAT_MIN <= lat <= USA_LAT_MAX) and (USA_LON_MIN <= lon <= USA_LON_MAX)

def geocode_text_new(text: str, api_key: str):
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.location,places.displayName,places.formattedAddress",
        "Content-Type": "application/json",
    }
    payload = {"textQuery": text, "regionCode": "US", "pageSize": 1}
    r = SESSION.post(PLACES_NEW_URL, headers=headers, json=payload, timeout=(20, 60))
    r.raise_for_status()
    data = r.json()
    places = data.get("places") or []
    if not places:
        return None

    loc = places[0].get("location") or {}
    lat = float(loc.get("latitude"))
    lon = float(loc.get("longitude"))
    if not _is_us(lat, lon):
        return None

    name = (places[0].get("displayName") or {}).get("text") or places[0].get("formattedAddress") or text
    return lat, lon, name

def compute_route(origin_lat, origin_lon, dest_lat, dest_lon, api_key: str):
    cache_key = f"route:{origin_lat:.5f},{origin_lon:.5f}->{dest_lat:.5f},{dest_lon:.5f}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline",
        "Content-Type": "application/json",
    }
    body = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}}},
        "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lon}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
    }

    r = SESSION.post(ROUTES_URL, headers=headers, json=body, timeout=(20, 90))
    r.raise_for_status()
    data = r.json()
    routes = data.get("routes") or []
    if not routes:
        return None

    out = {
        "distanceMeters": routes[0].get("distanceMeters", 0),
        "duration": routes[0].get("duration", "0s"),
        "encodedPolyline": ((routes[0].get("polyline") or {}).get("encodedPolyline")),
    }
    if not out["encodedPolyline"]:
        return None

    cache.set(cache_key, out, timeout=60 * 60 * 24)
    return out
