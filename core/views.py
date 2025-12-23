from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.serializers import RoutePlanRequestSerializer
from core.services.osrm_api import compute_route_osrm
from core.services.osm_api import geocode_us  # optional fallback (strings)

from core.services.fuel_plan import (
    decode_polyline, gather_candidates, attach_route_miles,
    choose_start_station, min_cost_plan
)

class RoutePlanView(APIView):
    def post(self, request):
        ser = RoutePlanRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        corridor_miles = float(data.get("corridor_miles", 10.0))
        max_range_miles = float(data.get("max_range_miles", 500.0))
        mpg = float(data.get("mpg", 10.0))

        # ----------------------------
        # start/end coords (NO GOOGLE)
        # ----------------------------
        if "start_latlng" in data and data["start_latlng"]:
            o_lat, o_lon = data["start_latlng"]["lat"], data["start_latlng"]["lon"]
            o_name = "start_latlng"
        else:
            # optional fallback: free OSM geocode
            got = geocode_us(data.get("start", ""))
            if not got:
                return Response(
                    {"error": "START_NOT_FOUND_OR_NOT_US", "hint": "Provide start_latlng to avoid geocoding."},
                    status=400
                )
            o_lat, o_lon, o_name = got

        if "end_latlng" in data and data["end_latlng"]:
            d_lat, d_lon = data["end_latlng"]["lat"], data["end_latlng"]["lon"]
            d_name = "end_latlng"
        else:
            got = geocode_us(data.get("end", ""))
            if not got:
                return Response(
                    {"error": "END_NOT_FOUND_OR_NOT_US", "hint": "Provide end_latlng to avoid geocoding."},
                    status=400
                )
            d_lat, d_lon, d_name = got

        # ----------------------------
        # FREE ROUTE (OSRM) - 1 call
        # ----------------------------
        route = compute_route_osrm(o_lat, o_lon, d_lat, d_lon)
        if not route:
            return Response({"error": "ROUTE_NOT_FOUND_OR_RATE_LIMIT"}, status=400)

        # OSRM polyline is polyline5 - make sure decode_polyline assumes precision=5
        poly = decode_polyline(route["encodedPolyline"])
        if len(poly) < 2:
            return Response({"error": "BAD_ROUTE_POLYLINE"}, status=400)

        # Pull fuel stops near route (DB only)
        candidates, cum = gather_candidates(poly, corridor_miles=corridor_miles)
        enriched = attach_route_miles(candidates, poly, cum)
        total_miles = float(cum[-1])

        # Ensure a station at start (cheapest within 25 miles)
        start_fs = choose_start_station(o_lat, o_lon, radius_miles=25.0)
        if not start_fs:
            return Response({"error": "NO_START_FUEL_STATION_NEAR_ORIGIN"}, status=400)

        stations = [{
            "fs": start_fs,
            "route_miles": 0.0,
            "price": float(start_fs.price),
        }]

        for x in enriched:
            fs = x["fs"]
            stations.append({
                "fs": fs,
                "route_miles": float(x["route_miles"]),
                "price": float(fs.price),
            })

        plan, err = min_cost_plan(
            stations,
            total_miles=total_miles,
            mpg=mpg,
            max_range_miles=max_range_miles
        )
        if err:
            return Response(err, status=400)

        gallons_used = total_miles / mpg

        return Response({
            "origin": {"name": o_name, "lat": o_lat, "lon": o_lon},
            "destination": {"name": d_name, "lat": d_lat, "lon": d_lon},
            "route": {
                "distance_miles": round(total_miles, 3),
                "distance_meters": route["distanceMeters"],
                "duration": route["duration"],
                "encoded_polyline": route["encodedPolyline"],
            },
            "vehicle": {
                "mpg": mpg,
                "max_range_miles": max_range_miles,
                "tank_capacity_gallons": plan["tank_gallons_capacity"],
                "gallons_used_total": round(gallons_used, 4),
            },
            "fuel_plan": {
                "stops": plan["stops"],
                "total_money_spent": plan["total_cost"],
            }
        }, status=status.HTTP_200_OK)
