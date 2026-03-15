import os
import math
import time
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

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
        "https://auth.fuelfinder.service.gov.uk/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "fuelfinder.read"
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _token_cache["token"]


# --- Haversine Distance ---
def haversine_miles(lat1, lon1, lat2, lon2):
    """Calculate distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# --- Fuel API ---
def fetch_nearby_stations(lat, lon, fuel_type="E10", radius_miles=5, max_results=10):
    """Fetch nearby stations from the UK Fuel Finder API."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Convert miles to km for API
    radius_km = radius_miles * 1.60934

    response = requests.get(
        "https://api.fuel-finder.service.gov.uk/v1/forecourts",
        headers=headers,
        params={
            "latitude": lat,
            "longitude": lon,
            "radius": radius_km,
            "fuel": fuel_type,
            "page_size": 50,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    stations = []
    for s in data.get("forecourts", []):
        prices = s.get("prices", {})
        price_pence = prices.get(fuel_type)

        # Normalise: some stations report in pounds (e.g. 1.39) vs pence (e.g. 139.9)
        if price_pence is not None:
            if price_pence < 10:
                price_pence = price_pence * 100
            if not (50 <= price_pence <= 500):
                price_pence = None

        if price_pence is None:
            continue

        s_lat = s.get("location", {}).get("latitude")
        s_lon = s.get("location", {}).get("longitude")
        if s_lat is None or s_lon is None:
            continue

        distance = haversine_miles(lat, lon, s_lat, s_lon)

        stations.append({
            "name": s.get("name", "Unknown Station"),
            "brand": s.get("brand", ""),
            "address": s.get("address", {}).get("full", ""),
            "latitude": s_lat,
            "longitude": s_lon,
            "distance_miles": round(distance, 2),
            "price_pence": round(price_pence, 1),
            "last_updated": s.get("price_last_updated", ""),
        })

    # Sort by effective cost (price + travel cost)
    purchase_litres = 50 / (price_pence / 100) if False else None  # calculated per station below
    cost_per_mile = 0.25

    for s in stations:
        travel_cost = s["distance_miles"] * 2 * cost_per_mile  # return journey
        fuel_cost = 50.0  # £50 of fuel
        # Total pence spent per litre equivalent
        s["travel_cost"] = round(travel_cost, 2)
        s["total_cost"] = round(fuel_cost + travel_cost, 2)
        s["effective_ppl"] = round(s["price_pence"] + (travel_cost * 100 / (50 / (s["price_pence"] / 100))), 1)

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
        fuel_type = request.args.get("fuel", "E10")  # E10=petrol, B7=diesel
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid or missing lat/lon parameters"}), 400

    try:
        results = fetch_nearby_stations(lat, lon, fuel_type=fuel_type)
        return jsonify({"stations": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
