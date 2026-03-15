import os
import math
import time
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BASE_URL = "https://api.fuelfinder.service.gov.uk"

# --- OAuth2 Token Management ---
_token_cache = {"token": None, "expires_at": 0}

def get_access_token():
    """Get a valid OAuth2 access token, refreshing if needed."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    client_id = os.environ.get("FUEL_CLIENT_ID")
    client_secret = os.environ.get("FUEL_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError("Missing FUEL_CLIENT_ID or FUEL_CLIENT_SECRET environment variables")

    response = requests.post(
        "https://api.fuelfinder.service.gov.uk/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "fuelfinder.read",
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _token_cache["token"]


def api_get(path, params=None):
    """Make an authenticated GET request to the Fuel Finder API."""
    token = get_access_token()
    response = requests.get(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


# --- Haversine Distance ---
def haversine_miles(lat1, lon1, lat2, lon2):
    """Calculate distance in miles between two lat/lon points."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# --- Main Logic ---
def fetch_nearby_stations(lat, lon, fuel_type="E10", radius_miles=5, max_results=10):
    """Fetch nearby stations and their prices from the UK Fuel Finder API."""

    # Step 1: Get all forecourts (locations)
    forecourts_data = api_get("/forecourts")
    forecourts = forecourts_data if isinstance(forecourts_data, list) else forecourts_data.get("forecourts", [])

    # Step 2: Get all prices
    prices_data = api_get("/prices")
    prices_list = prices_data if isinstance(prices_data, list) else prices_data.get("prices", [])

    # Step 3: Build a prices lookup dict keyed by node_id
    prices_by_node = {}
    for p in prices_list:
        node_id = p.get("node_id")
        if node_id:
            prices_by_node[node_id] = {
                fp["fuel_type"]: fp["price"]
                for fp in p.get("fuel_prices", [])
            }

    # Step 4: Filter by distance and fuel type, merge data
    cost_per_mile = 0.25
    stations = []

    for s in forecourts:
        node_id = s.get("node_id")
        location = s.get("location", {})
        s_lat = location.get("latitude")
        s_lon = location.get("longitude")

        if s_lat is None or s_lon is None:
            continue

        distance = haversine_miles(lat, lon, s_lat, s_lon)
        if distance > radius_miles:
            continue

        # Get price for requested fuel type
        station_prices = prices_by_node.get(node_id, {})

        # Handle diesel variants
        price = None
        if fuel_type == "B7":
            price = station_prices.get("B7_STANDARD") or station_prices.get("B7_PREMIUM") or station_prices.get("B7")
        else:
            price = station_prices.get(fuel_type)

        if price is None:
            continue

        # Normalise price: some report in pounds (1.39) vs pence (139.9)
        if price < 10:
            price = price * 100
        if not (50 <= price <= 500):
            continue

        travel_cost = round(distance * 2 * cost_per_mile, 2)
        total_cost = round(50.0 + travel_cost, 2)
        litres = round(50 / (price / 100), 1)

        address = location.get("address_line_1", "")
        city = location.get("city", "")
        full_address = f"{address}, {city}".strip(", ")

        stations.append({
            "name": s.get("trading_name", "Unknown Station"),
            "brand": s.get("brand_name", ""),
            "address": full_address,
            "latitude": s_lat,
            "longitude": s_lon,
            "distance_miles": round(distance, 2),
            "price_pence": round(price, 1),
            "travel_cost": travel_cost,
            "total_cost": total_cost,
            "litres": litres,
        })

    # Sort by total cost (fuel + travel)
    stations.sort(key=lambda x: x["total_cost"])
    return stations[:max_results]


# --- Routes ---
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stations")
def stations():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
        fuel_type = request.args.get("fuel", "E10")
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid or missing lat/lon parameters"}), 400

    try:
        results = fetch_nearby_stations(lat, lon, fuel_type=fuel_type)
        return jsonify({"stations": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
