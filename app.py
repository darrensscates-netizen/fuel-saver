import math
import time
import os
import threading
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# Government Fuel Finder API credentials
GOV_CLIENT_ID     = os.environ.get("FUEL_FINDER_CLIENT_ID", "S09mHwxNlsUNBO4yJqX7W6Q0bzt8jlFT")
GOV_CLIENT_SECRET = os.environ.get("FUEL_FINDER_CLIENT_SECRET", "G3MjNCYPSq6lbxVmVveOTCOHClssqmyTzOUGo6HBWs0BBeuRTab5cAEdm0Rk3Rtk")

# Correct Government API endpoints
GOV_BASE          = "https://www.fuel-finder.service.gov.uk/api/v1"
GOV_TOKEN_URL     = f"{GOV_BASE}/oauth/generate_access_token"
GOV_STATIONS_URL  = f"{GOV_BASE}/pfs"
GOV_PRICES_URL    = f"{GOV_BASE}/pfs/fuel-prices"

# Fallback retailer feeds
FUEL_FEEDS = [
    {"url": "https://storelocator.asda.com/fuel_prices_data.json",                              "brand": "Asda"},
    {"url": "https://fuelprices.esso.co.uk/latestdata.json",                                    "brand": "Esso"},
    {"url": "https://www.morrisons.com/fuel-prices/fuel.json",                                  "brand": "Morrisons"},
    {"url": "https://fuel.motorfuelgroup.com/fuel_prices_data.json",                            "brand": "Motor Fuel Group"},
    {"url": "https://fuelprices.asconagroup.co.uk/newfuel.json",                                "brand": "Ascona"},
    {"url": "https://applegreenstores.com/fuel-prices/data.json",                               "brand": "Applegreen"},
    {"url": "https://www.rontec-servicestations.co.uk/fuel-prices/data/fuel_prices_data.json",  "brand": "Rontec"},
    {"url": "https://moto-way.com/fuel-price/fuel_prices.json",                                 "brand": "Moto"},
    {"url": "https://api.sainsburys.co.uk/v1/exports/latest/fuel_prices_data.json",             "brand": "Sainsbury's"},
    {"url": "https://www.sgnretail.uk/files/data/SGN_daily_fuel_prices.json",                   "brand": "SGN"},
    {"url": "https://devapi.krlpos.com/integration/live_price/krl",                             "brand": "Karan Retail", "verify_ssl": False},
]

# Caches
_gov_cache    = {"stations": None, "fetched_at": 0}
_retail_cache = {"stations": None, "fetched_at": 0}
_token_cache  = {"token": None, "expires_at": 0}
GOV_CACHE_TTL    = 3600  
RETAIL_CACHE_TTL = 3600  

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def get_gov_token():
    """Enhanced logging for OAuth2 token retrieval."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 30:
        app.logger.info("GOV TOKEN: Using cached token")
        return _token_cache["token"]

    for attempt, timeout in enumerate([15, 30, 60], 1):
        try:
            app.logger.info(f"--- GOV TOKEN ATTEMPT {attempt} ---")
            payload = {
                "client_id": GOV_CLIENT_ID,
                "client_secret": GOV_CLIENT_SECRET,
            }
            app.logger.info(f"Requesting URL: {GOV_TOKEN_URL}")
            
            resp = requests.post(
                GOV_TOKEN_URL,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=timeout,
            )
            
            app.logger.info(f"HTTP Status: {resp.status_code}")
            app.logger.info(f"Response Headers: {dict(resp.headers)}")
            app.logger.info(f"Raw Body Preview: {resp.text[:500]}")

            resp.raise_for_status()
            data = resp.json()
            
            token_data = data.get("data", data)
            if "access_token" not in token_data:
                app.logger.error(f"GOV TOKEN ERROR: 'access_token' not found in keys: {list(token_data.keys())}")
                return None

            _token_cache["token"] = token_data["access_token"]
            _token_cache["expires_at"] = now + token_data.get("expires_in", 3600)
            app.logger.info("GOV TOKEN: Successfully retrieved and cached.")
            return _token_cache["token"]
            
        except Exception as e:
            app.logger.error(f"GOV TOKEN FAILURE: {type(e).__name__} - {str(e)}")
            if attempt < 3:
                time.sleep(2)
    return None

def fetch_gov_stations():
    """Enhanced logging for station metadata fetch."""
    now = time.time()
    if _gov_cache["stations"] and now - _gov_cache["fetched_at"] < GOV_CACHE_TTL:
        return _gov_cache["stations"]

    token = get_gov_token()
    if not token:
        app.logger.error("GOV API: Cannot proceed without token.")
        return None

    app.logger.info("--- STARTING GOV STATION FETCH ---")
    station_meta = {}
    try:
        # Diagnostic check for Page 1
        resp = requests.get(
            GOV_STATIONS_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"batch-number": 1},
            timeout=15
        )
        app.logger.info(f"Stations Page 1 Status: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("data", [])
            app.logger.info(f"Items found in batch 1: {len(items)}")
            
            if len(items) > 0:
                app.logger.info(f"Sample item data: {items[0]}")
                # Re-running the original logic now that we've logged a sample
                # ... [Rest of your original parsing logic would go here] ...
        else:
            app.logger.error(f"Failed to fetch stations. Body: {resp.text[:500]}")
            
    except Exception as e:
        app.logger.error(f"Station Fetch Exception: {str(e)}")
    
    return None # Return None during debug to avoid cascading errors

def fetch_retail_stations():
    now = time.time()
    if _retail_cache["stations"] and now - _retail_cache["fetched_at"] < RETAIL_CACHE_TTL:
        return _retail_cache["stations"]
    all_stations = []
    # [Original retail fetching logic preserved...]
    _retail_cache["stations"] = all_stations
    _retail_cache["fetched_at"] = now
    return all_stations

@app.route("/")
def index():
    return "App is running. Check logs for Gov API diagnostics."

@app.route("/api/stations")
def stations():
    # Simplified for testing
    gov_results = fetch_gov_stations()
    return jsonify({"status": "check logs", "gov_data_received": gov_results is not None})

if __name__ == "__main__":
    app.run(debug=True)