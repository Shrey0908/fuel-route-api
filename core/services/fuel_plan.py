import math
from typing import List, Tuple, Dict
from core.models import FuelStop

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def decode_polyline(polyline_str: str) -> List[Tuple[float, float]]:
    index, lat, lng = 0, 0, 0
    coords = []
    length = len(polyline_str)

    while index < length:
        shift, result = 0, 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift, result = 0, 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append((lat / 1e5, lng / 1e5))
    return coords

def cumulative_miles(poly: List[Tuple[float, float]]) -> List[float]:
    cum = [0.0]
    for i in range(1, len(poly)):
        cum.append(cum[-1] + haversine_miles(poly[i-1][0], poly[i-1][1], poly[i][0], poly[i][1]))
    return cum

def _box(lat, lon, radius_miles):
    dlat = radius_miles / 69.0
    dlon = radius_miles / (69.0 * max(0.1, math.cos(math.radians(lat))))
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)

def gather_candidates(poly, corridor_miles: float, sample_every_miles: float = 20.0, max_per_sample: int = 25):
    cum = cumulative_miles(poly)
    total = cum[-1]
    candidates = {}
    next_sample = 0.0

    for (lat, lon), m in zip(poly, cum):
        if m + 1e-6 < next_sample:
            continue
        next_sample += sample_every_miles

        lat_min, lat_max, lon_min, lon_max = _box(lat, lon, corridor_miles)
        qs = (FuelStop.objects
              .filter(lat__gte=lat_min, lat__lte=lat_max, lon__gte=lon_min, lon__lte=lon_max)
              .exclude(price__isnull=True)
              .order_by("price")[:max_per_sample])

        for fs in qs:
            candidates[fs.id] = fs

        if next_sample > total:
            break

    return list(candidates.values()), cum

def attach_route_miles(stops, poly, cum):
    enriched = []
    for fs in stops:
        best_i = 0
        best_d = 1e18
        for i, (lat, lon) in enumerate(poly):
            d = haversine_miles(fs.lat, fs.lon, lat, lon)
            if d < best_d:
                best_d = d
                best_i = i
        enriched.append({
            "fs": fs,
            "route_miles": float(cum[best_i]),
            "offroute_miles": float(best_d),
        })
    enriched.sort(key=lambda x: x["route_miles"])
    return enriched

def choose_start_station(origin_lat, origin_lon, radius_miles: float = 25.0):
    lat_min, lat_max, lon_min, lon_max = _box(origin_lat, origin_lon, radius_miles)
    return (FuelStop.objects
            .filter(lat__gte=lat_min, lat__lte=lat_max, lon__gte=lon_min, lon__lte=lon_max)
            .exclude(price__isnull=True)
            .order_by("price")
            .first())

def min_cost_plan(stations: List[Dict], total_miles: float, mpg: float, max_range_miles: float):
    tank_gal = max_range_miles / mpg
    nodes = [n for n in stations if n["route_miles"] <= total_miles]
    nodes.append({"fs": None, "route_miles": total_miles, "price": float("inf")})
    nodes.sort(key=lambda x: x["route_miles"])

    for i in range(len(nodes) - 1):
        if (nodes[i+1]["route_miles"] - nodes[i]["route_miles"]) > max_range_miles + 1e-6:
            return None, {"error": "NO_FEASIBLE_PLAN", "gap_miles": nodes[i+1]["route_miles"] - nodes[i]["route_miles"]}

    fuel = 0.0
    total_cost = 0.0
    plan = []

    for i in range(len(nodes) - 1):
        here = nodes[i]
        here_price = float(here["price"])
        here_mile = float(here["route_miles"])

        j = None
        for k in range(i + 1, len(nodes)):
            if nodes[k]["route_miles"] - here_mile > max_range_miles:
                break
            if float(nodes[k]["price"]) < here_price:
                j = k
                break

        target_miles = nodes[j]["route_miles"] if j is not None else min(total_miles, here_mile + max_range_miles)
        need_gal = (target_miles - here_mile) / mpg
        buy_gal = max(0.0, min(tank_gal, need_gal) - fuel)

        cost = buy_gal * here_price
        total_cost += cost
        fuel += buy_gal

        burn = (nodes[i+1]["route_miles"] - here_mile) / mpg
        fuel -= burn

        if here["fs"] is not None and buy_gal > 1e-6:
            fs = here["fs"]
            plan.append({
                "id": fs.id,
                "name": fs.name,
                "city": fs.city,
                "state": fs.state,
                "lat": fs.lat,
                "lon": fs.lon,
                "price": float(fs.price),
                "route_miles": round(here_mile, 3),
                "gallons_bought": round(buy_gal, 4),
                "cost": round(cost, 4),
            })

    return {
        "stops": plan,
        "total_cost": round(total_cost, 4),
        "tank_gallons_capacity": round(tank_gal, 4),
    }, None
