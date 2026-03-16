import math
import time
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# Public JSON feeds from major UK fuel retailers - no auth needed
FUEL_FEEDS = [
    {"url": "https://storelocator.asda.com/fuel_prices_data.json", "brand": "Asda"},
    {"url": "https://www.bp.com/en_gb/united-kingdom/home/fuelprices/fuel_prices_data.json", "brand": "BP"},
    {"url": "https://fuelprices.esso.co.uk/latestdata.json", "brand": "Esso"},
    {"url": "https://www.morrisons.com/fuel-prices/fuel.json", "brand": "Morrisons"},
    {"url": "https://jetlocal.co.uk/fuel_prices_data.json", "brand": "Jet"},
    {"url": "https://fuel.motorfuelgroup.com/fuel_prices_data.json", "brand": "Motor Fuel Group"},
    {"url": "https://fuelprices.asconagroup.co.uk/newfuel.json", "brand": "Ascona"},
    {"url": "https://applegreenstores.com/fuel-prices/data.json", "brand": "Applegreen"},
    {"url": "https://www.rontec-servicestations.co.uk/fuel-prices/data/fuel_prices_data.json", "brand": "Rontec"},
    {"url": "https://moto-way.com/fuel-price/fuel_prices.json", "brand": "Moto"},
]

# Cache for 30 minutes
_cache = {"stations": None, "fetched_at": 0}
CACHE_TTL = 1800


def parse_feed(data, default_brand):
    """Parse a standard UK fuel price JSON feed into a list of stations."""
    stations = []
    entries = data if isinstance(data, list) else data.get("stations", data.get("S", []))

    for s in entries:
        try:
            # Location - handle different feed formats
            loc = s.get("location") or s
            lat = float(loc.get("lat") or loc.get("latitude") or s.get("lat") or 0)
            lon = float(loc.get("lng") or loc.get("longitude") or loc.get("lon") or s.get("lng") or 0)
            if lat == 0 or lon == 0:
                continue

            # Prices - handle different feed formats
            prices = s.get("prices") or s.get("fuel_prices") or {}
            if isinstance(prices, list):
                prices = {p.get("fuel_type", p.get("name", "")): p.get("price", p.get("cost", 0)) for p in prices}

            def get_price(keys):
                for k in keys:
                    v = prices.get(k)
                    if v:
                        try:
                            p = float(v)
                            if p < 10: p *= 100
                            if 50 <= p <= 500:
                                return round(p, 1)
                        except (ValueError, TypeError):
                            pass
                return None

            e10 = get_price(["E10", "e10", "Unleaded", "unleaded", "petrol"])
            e5  = get_price(["E5", "e5", "Super", "super", "super_unleaded"])
            b7  = get_price(["B7", "b7", "Diesel", "diesel", "B7_STANDARD", "B7_PREMIUM"])

            if not any([e10, e5, b7]):
                continue

            stations.append({
                "name": s.get("name") or s.get("site_name") or s.get("Name") or default_brand,
                "brand": s.get("brand") or default_brand,
                "address": s.get("address") or s.get("Address") or "",
                "latitude": lat,
                "longitude": lon,
                "e10": e10,
                "e5": e5,
                "b7": b7,
            })
        except (ValueError, KeyError, TypeError):
            continue
    return stations


def fetch_all_stations():
    """Fetch and cache stations from all fuel feeds."""
    now = time.time()
    if _cache["stations"] and now - _cache["fetched_at"] < CACHE_TTL:
        return _cache["stations"]

    all_stations = []
    for feed in FUEL_FEEDS:
        try:
            resp = requests.get(feed["url"], timeout=10)
            resp.raise_for_status()
            parsed = parse_feed(resp.json(), feed["brand"])
            all_stations.extend(parsed)
        except Exception:
            continue  # Skip failed feeds silently

    _cache["stations"] = all_stations
    _cache["fetched_at"] = now
    return all_stations


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearby_stations(lat, lon, fuel_type="e10", radius_miles=5, max_results=10):
    all_stations = fetch_all_stations()
    fuel_key = fuel_type.lower()
    cost_per_mile = 0.25
    results = []

    for s in all_stations:
        price = s.get(fuel_key)
        if price is None:
            continue

        distance = haversine_miles(lat, lon, s["latitude"], s["longitude"])
        if distance > radius_miles:
            continue

        fuel_cost = round(30 * (price / 100), 2)  # cost of 30 litres
        travel_cost = round(distance * 2 * cost_per_mile, 2)
        total_cost = round(fuel_cost + travel_cost, 2)
        driving_mins = max(1, round(distance / 20 * 60))  # 20mph average local speed

        results.append({
            "name": s["name"],
            "brand": s["brand"],
            "address": s["address"],
            "latitude": s["latitude"],
            "longitude": s["longitude"],
            "distance_miles": round(distance, 2),
            "price_pence": price,
            "fuel_cost": fuel_cost,
            "travel_cost": travel_cost,
            "total_cost": total_cost,
            "litres": 30,
            "driving_mins": driving_mins,
        })

    results.sort(key=lambda x: x["total_cost"])
    return results[:max_results]


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return "OK", 200


@app.route("/api/stations")
def stations():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
        fuel_type = request.args.get("fuel", "e10").lower()
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid or missing lat/lon parameters"}), 400

    try:
        results = find_nearby_stations(lat, lon, fuel_type=fuel_type)
        return jsonify({"stations": results, "count": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
