import requests
from django.core.cache import cache

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "fuel-route-api/1.0",
    "Accept": "application/json",
})

OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"

def compute_route_osrm(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float):
    """
    FREE routing (public OSRM server)
    Returns: dict with distanceMeters, duration, encodedPolyline
    """
    cache_key = f"osrm:{origin_lat:.5f},{origin_lon:.5f}->{dest_lat:.5f},{dest_lon:.5f}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    params = {
        "overview": "full",
        "geometries": "polyline",   # OSRM uses polyline5 (1e5)
        "alternatives": "false",
        "steps": "false",
    }
    url = f"{OSRM_ROUTE_URL}/{coords}"

    r = SESSION.get(url, params=params, timeout=(15, 90))
    if r.status_code == 429:
        return None
    r.raise_for_status()

    data = r.json()
    routes = data.get("routes") or []
    if not routes:
        return None

    out = {
        "distanceMeters": int(routes[0].get("distance", 0)),
        "duration": f'{int(routes[0].get("duration", 0))}s',
        "encodedPolyline": routes[0].get("geometry"),
    }
    if not out["encodedPolyline"]:
        return None

    cache.set(cache_key, out, timeout=60 * 60 * 24)
    return out
