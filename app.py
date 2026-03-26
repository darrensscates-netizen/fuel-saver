import math
import time
import os
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# Government Fuel Finder API credentials
GOV_CLIENT_ID     = os.environ.get("FUEL_FINDER_CLIENT_ID", "iseOU59nzpUdWbXZGzMEAhZyNBDKVep5")
GOV_CLIENT_SECRET = os.environ.get("FUEL_FINDER_CLIENT_SECRET", "p63NjcFQqArVmx56RmkBjroLxOYSRHIUwx7b3rKxvbubrMkhvHDHXumXihUlg9Yk")
GOV_TOKEN_URL     = "https://api.fuel-finder.service.gov.uk/oauth/token"
GOV_STATIONS_URL  = "https://api.fuel-finder.service.gov.uk/api/v1/pfs"
GOV_PRICES_URL    = "https://api.fuel-finder.service.gov.uk/api/v1/pfs/fuel-prices"

# Fallback retailer feeds
FUEL_FEEDS = [
    {"url": "https://storelocator.asda.com/fuel_prices_data.json",                              "brand": "Asda"},
    {"url": "https://www.bp.com/en_gb/united-kingdom/home/fuelprices/fuel_prices_data.json",    "brand": "BP"},
    {"url": "https://fuelprices.esso.co.uk/latestdata.json",                                    "brand": "Esso"},
    {"url": "https://www.morrisons.com/fuel-prices/fuel.json",                                  "brand": "Morrisons"},
    {"url": "https://jetlocal.co.uk/fuel_prices_data.json",                                     "brand": "Jet"},
    {"url": "https://fuel.motorfuelgroup.com/fuel_prices_data.json",                            "brand": "Motor Fuel Group"},
    {"url": "https://fuelprices.asconagroup.co.uk/newfuel.json",                                "brand": "Ascona"},
    {"url": "https://applegreenstores.com/fuel-prices/data.json",                               "brand": "Applegreen"},
    {"url": "https://www.rontec-servicestations.co.uk/fuel-prices/data/fuel_prices_data.json",  "brand": "Rontec"},
    {"url": "https://moto-way.com/fuel-price/fuel_prices.json",                                 "brand": "Moto"},
]

# Caches
_gov_cache    = {"stations": None, "fetched_at": 0}
_retail_cache = {"stations": None, "fetched_at": 0}
_token_cache  = {"token": None, "expires_at": 0}
GOV_CACHE_TTL    = 1800
RETAIL_CACHE_TTL = 1800


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_gov_token():
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 30:
        return _token_cache["token"]
    try:
        resp = requests.post(
            GOV_TOKEN_URL,
            data={
                "grant_type":    "client_credentials",
                "client_id":     GOV_CLIENT_ID,
                "client_secret": GOV_CLIENT_SECRET,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"]      = data["access_token"]
        _token_cache["expires_at"] = now + data.get("expires_in", 3600)
        return _token_cache["token"]
    except Exception:
        return None


def fetch_gov_stations():
    now = time.time()
    if _gov_cache["stations"] and now - _gov_cache["fetched_at"] < GOV_CACHE_TTL:
        return _gov_cache["stations"]

    token = get_gov_token()
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}

    # Fetch station metadata with pagination
    station_meta = {}
    page = 1
    while True:
        try:
            resp = requests.get(GOV_STATIONS_URL, headers=headers,
                                params={"page": page, "per_page": 500}, timeout=20)
            resp.raise_for_status()
            data  = resp.json()
            items = data if isinstance(data, list) else data.get("data", data.get("stations", []))
            if not items:
                break
            for s in items:
                sid = s.get("id") or s.get("pfs_id")
                if not sid:
                    continue
                try:
                    lat = float(s.get("latitude") or s.get("lat") or 0)
                    lon = float(s.get("longitude") or s.get("lng") or s.get("lon") or 0)
                except (TypeError, ValueError):
                    continue
                if lat == 0 or lon == 0:
                    continue
                station_meta[str(sid)] = {
                    "name":      s.get("name") or s.get("trading_name") or s.get("brand") or "Unknown",
                    "brand":     s.get("brand") or s.get("operator") or "Unknown",
                    "address":   s.get("address") or s.get("street_address") or "",
                    "latitude":  lat,
                    "longitude": lon,
                    "e10": None, "e5": None, "b7": None,
                }
            if len(items) < 500:
                break
            page += 1
        except Exception:
            break

    if not station_meta:
        return None

    # Fetch fuel prices with pagination
    page = 1
    while True:
        try:
            resp = requests.get(GOV_PRICES_URL, headers=headers,
                                params={"page": page, "per_page": 500}, timeout=20)
            resp.raise_for_status()
            data  = resp.json()
            items = data if isinstance(data, list) else data.get("data", data.get("prices", []))
            if not items:
                break
            for p in items:
                sid  = str(p.get("pfs_id") or p.get("id") or "")
                if sid not in station_meta:
                    continue
                fuel = str(p.get("fuel_type") or "").upper().replace(" ", "_")
                try:
                    price = float(p.get("price") or 0)
                    if price < 10:
                        price *= 100
                    if not (50 <= price <= 500):
                        continue
                    price = round(price, 1)
                except (TypeError, ValueError):
                    continue
                if fuel in ("E10", "UNLEADED", "PETROL"):
                    station_meta[sid]["e10"] = price
                elif fuel in ("E5", "SUPER_UNLEADED", "SUPER"):
                    station_meta[sid]["e5"] = price
                elif fuel in ("B7", "DIESEL", "B7_STANDARD", "B7_PREMIUM"):
                    station_meta[sid]["b7"] = price
            if len(items) < 500:
                break
            page += 1
        except Exception:
            break

    stations = [s for s in station_meta.values() if any([s["e10"], s["e5"], s["b7"]])]
    if not stations:
        return None

    _gov_cache["stations"]   = stations
    _gov_cache["fetched_at"] = now
    return stations


def parse_feed(data, default_brand):
    stations = []
    entries = data if isinstance(data, list) else data.get("stations", data.get("S", []))
    for s in entries:
        try:
            loc = s.get("location") or s
            lat = float(loc.get("lat") or loc.get("latitude") or s.get("lat") or 0)
            lon = float(loc.get("lng") or loc.get("longitude") or loc.get("lon") or s.get("lng") or 0)
            if lat == 0 or lon == 0:
                continue
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
            e5  = get_price(["E5",  "e5",  "Super",    "super",    "super_unleaded"])
            b7  = get_price(["B7",  "b7",  "Diesel",   "diesel",   "B7_STANDARD", "B7_PREMIUM"])
            if not any([e10, e5, b7]):
                continue
            stations.append({
                "name": s.get("name") or s.get("site_name") or s.get("Name") or default_brand,
                "brand": s.get("brand") or default_brand,
                "address": s.get("address") or s.get("Address") or "",
                "latitude": lat, "longitude": lon,
                "e10": e10, "e5": e5, "b7": b7,
            })
        except (ValueError, KeyError, TypeError):
            continue
    return stations


def fetch_retail_stations():
    now = time.time()
    if _retail_cache["stations"] and now - _retail_cache["fetched_at"] < RETAIL_CACHE_TTL:
        return _retail_cache["stations"]
    all_stations = []
    for feed in FUEL_FEEDS:
        try:
            resp = requests.get(feed["url"], timeout=10)
            resp.raise_for_status()
            all_stations.extend(parse_feed(resp.json(), feed["brand"]))
        except Exception:
            continue
    _retail_cache["stations"]   = all_stations
    _retail_cache["fetched_at"] = now
    return all_stations


def fetch_all_stations():
    gov = fetch_gov_stations()
    if gov:
        return gov, "gov"
    return fetch_retail_stations(), "retail"


def find_nearby_stations(lat, lon, fuel_type="e10", radius_miles=10):
    all_stations, source = fetch_all_stations()
    fuel_key      = fuel_type.lower()
    cost_per_mile = 0.25
    results       = []
    scaled_max    = max(10, int(radius_miles * 1.5))

    for s in all_stations:
        price = s.get(fuel_key)
        if price is None:
            continue
        distance = haversine_miles(lat, lon, s["latitude"], s["longitude"])
        if distance > radius_miles:
            continue
        fuel_cost    = round(30 * (price / 100), 2)
        travel_cost  = round(distance * 2 * cost_per_mile, 2)
        total_cost   = round(fuel_cost + travel_cost, 2)
        driving_mins = max(1, round(distance / 20 * 60))
        results.append({
            "name":           s["name"],
            "brand":          s["brand"],
            "address":        s["address"],
            "latitude":       s["latitude"],
            "longitude":      s["longitude"],
            "distance_miles": round(distance, 2),
            "price_pence":    price,
            "fuel_cost":      fuel_cost,
            "travel_cost":    travel_cost,
            "total_cost":     total_cost,
            "litres":         30,
            "driving_mins":   driving_mins,
        })

    results.sort(key=lambda x: x["total_cost"])
    return results[:scaled_max], source


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return "OK", 200

@app.route("/ads.txt")
def ads_txt():
    return "google.com, pub-7146582862091752, DIRECT, f08c47fec0942fa0\n", 200, {"Content-Type": "text/plain"}

@app.route("/api/stations")
def stations():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
        fuel_type = request.args.get("fuel", "e10").lower()
        try:
            radius = float(request.args.get("radius", 10))
            radius = max(1, min(radius, 50))
        except (TypeError, ValueError):
            radius = 10
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid or missing lat/lon parameters"}), 400

    try:
        results, source = find_nearby_stations(lat, lon, fuel_type=fuel_type, radius_miles=radius)
        return jsonify({"stations": results, "count": len(results), "radius": radius, "source": source})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
