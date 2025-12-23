# Fuel Route API (Django + DRF)

A Django REST API that generates a **fuel-optimized stop plan** for a vehicle traveling from an origin to a destination.
It uses a route polyline (from your routing provider) and finds candidate fuel stations along the corridor, then
builds a plan that respects vehicle range and estimates total spend.

> ✅ Built for an assignment-style deliverable: clean structure, reproducible steps, and **no secret keys in Git**.

---

## What this project does

Given:
- **Origin / Destination** lat-lon
- **Route distance + encoded polyline**
- **Vehicle** MPG + tank + max range

It returns:
- A list of **fuel stops** (stations) with price, distance along route, gallons to buy, and cost
- Total money spent

---

## High-level architecture

```
Client (Postman / Frontend)
        |
        |  POST /api/route-plan/
        v
Django REST View (core/views.py)
        |
        |  validates request, extracts polyline + vehicle params
        v
Fuel Planning Service (core/services/fuel_plan.py)
   |        |
   |        +-- Candidate gathering (FuelStop DB query)
   |        +-- Corridor logic + planning algorithm
   |
   +--> Routing helpers (core/services/osrm_api.py)   [optional, if used]
   +--> Places/geocode helpers (core/services/google_api.py / osm_api.py) [optional]
        |
        v
SQLite DB (db.sqlite3) with FuelStop + pricing fields
```

### Key components
- **`core/views.py`**: DRF endpoint(s)
- **`core/serializers.py`**: request/response validation
- **`core/services/fuel_plan.py`**: main planning logic
- **`core/models.py`**: FuelStop model (fields like name, address, city, state, lat, lon, price, etc.)
- **`core/management/commands/*`**: one-time data utilities (load prices, geocode, export CSV)

---

## Folder structure

```
fuel-route-api/
  config/                     # Django project settings + urls
  core/                       # Main app
    services/                 # Business logic (fuel plan, routing helpers)
    management/commands/      # CLI commands for data bootstrapping
    migrations/               # DB schema
    views.py                  # API endpoints
    serializers.py            # DRF serializers
    models.py                 # DB models
  manage.py
  fuel-prices-for-be-assessment.csv
  (optional) fuel-stops-with-latlon.csv
```

---

## Running locally (Windows / PowerShell)

### 1) Create & activate venv
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2) Install dependencies
If you have `requirements.txt`:
```powershell
pip install -r requirements.txt
```
If not, minimum expected packages:
```powershell
pip install django djangorestframework requests python-dotenv
```

### 3) Migrate DB
```powershell
python manage.py migrate
```

### 4) Load / seed data
This repo usually expects fuel stations & prices in the DB. You have a few options:

#### Option A — Load prices from the provided CSV (recommended)
```powershell
python manage.py load_fuel_prices
```
> The command may read `fuel-prices-for-be-assessment.csv` internally (depending on implementation).
If your command requires a path, try:
```powershell
python manage.py load_fuel_prices --help
```

#### Option B — Use your “lat/lon ready” CSV (no external geocoding)
If you already generated a CSV that contains `lat` and `lon`, import those into your DB (one-time).
If you don’t already have an import command, the quickest safe way is via Django shell:

```powershell
python manage.py shell
```

Then run (adjust CSV path/name as needed):
```python
import csv
from core.models import FuelStop

path = "fuel-stops-with-latlon.csv"
with open(path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        FuelStop.objects.update_or_create(
            # pick a stable unique key; if opis_id exists, use it:
            opis_id=row.get("opis_id") or None,
            defaults={
                "name": row.get("name","").strip(),
                "address": row.get("address","").strip(),
                "city": row.get("city","").strip(),
                "state": row.get("state","").strip(),
                "rack_id": row.get("rack_id") or None,
                "price": float(row["price"]) if row.get("price") else None,
                "lat": float(row["lat"]) if row.get("lat") else None,
                "lon": float(row["lon"]) if row.get("lon") else None,
            }
        )
print("Done")
```

> ✅ Once lat/lon exists in DB, you **do not need geolocation APIs again**.

---

## Start the server

```powershell
python manage.py runserver
```

Server will run at:
- `http://127.0.0.1:8000/`

---

## API Usage

### `POST /api/route-plan/`

**Example request body (minimal):**
```json
{
  "origin": {"name":"start_latlng","lat":40.7128,"lon":-74.006},
  "destination": {"name":"end_latlng","lat":41.8781,"lon":-87.6298},
  "route": {
    "distance_miles": 788.966,
    "distance_meters": 1272193,
    "duration": "53414s",
    "encoded_polyline": "<polyline>"
  },
  "vehicle": {
    "mpg": 10.0,
    "max_range_miles": 500.0,
    "tank_capacity_gallons": 50.0
  }
}
```

**Example response (shape):**
```json
{
  "fuel_plan": {
    "stops": [
      {
        "id": 1320,
        "name": "BOLLA MARKET",
        "city": "Elizabeth",
        "state": "NJ",
        "lat": 40.6512086,
        "lon": -74.2184278,
        "price": 3.099,
        "route_miles": 0.0,
        "gallons_bought": 6.4571,
        "cost": 20.0107
      }
    ],
    "total_money_spent": 241.1016
  }
}
```

---

## Management commands (one-time utilities)

Located in: `core/management/commands/`

### Export fuel stops to CSV
```powershell
python manage.py export_fuel_stops_csv --help
python manage.py export_fuel_stops_csv
```

### Geocode commands (optional)
These exist, but **you don’t need them** if your DB already has lat/lon.

- Free (no API key): Overpass
```powershell
python manage.py geocode_fuel_stops_overpass --help
python manage.py geocode_fuel_stops_overpass
```

- Paid / key-based (DO NOT commit keys):
```powershell
python manage.py geocode_fuel_stops_google
python manage.py geocode_fuel_stops_places_openai
python manage.py geocode_fuel_stops_mapsco
```

---

## Environment variables (keep secrets out of Git)

Create a local `.env` file (DO NOT commit it):

```env
# Required
DJANGO_SECRET_KEY=your_key_here

# Optional keys (only needed if you use those geocoders)
GOOGLE_PLACES_API_KEY=your_key_here
GOOGLE_MAPS_API_KEY=your_key_here
GEOCODE_MAPS_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-5-mini
```

### ✅ `.gitignore` must include
```
.env
.env.*
.venv/
db.sqlite3
```

---

## Security checklist (before pushing to GitHub)

1) Ensure `.env` is ignored
```powershell
git status
git ls-files | findstr ".env"
```

2) Ensure venv is ignored
```powershell
git ls-files | findstr ".venv"
```

3) Ensure your DB is ignored (recommended)
```powershell
git ls-files | findstr "db.sqlite3"
```

---

## Common issues

### FieldError: Cannot resolve keyword 'retail_price'
Your model field is named **`price`** (not `retail_price`).
Update any queryset filters like:
```python
.exclude(price__isnull=True)
```
instead of `retail_price__isnull=True`.

### “Nothing to geocode.”
It means there are no DB rows missing `lat/lon` (or nothing matches your command filters).
If your data already has lat/lon, you’re good ✅

---

## How to push this README to GitHub

From project root:
```powershell
git add README.md
git commit -m "Add README"
git push
```

---

## License
For assignment/demo use.
