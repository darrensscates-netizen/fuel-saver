import math
import time
import os
import threading
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


@app.before_request
def redirect_www():
    """Redirect www to non-www for canonical URL."""
    from flask import redirect, request
    host = request.host
    if host and host.startswith("www."):
        url = request.url.replace("://www.", "://", 1)
        return redirect(url, code=301)


def _background_cache_refresh():
    """Background thread that pre-warms the cache every 55 minutes."""
    while True:
        time.sleep(55 * 60)  # Wait 55 mins then refresh
        try:
            app.logger.info("Background cache refresh starting...")
            fetch_retail_stations()
            app.logger.info("Background cache refresh complete.")
        except Exception as e:
            app.logger.error(f"Background cache refresh error: {e}")

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
    # {"url": "https://www.bp.com/en_gb/united-kingdom/home/fuelprices/fuel_prices_data.json",    "brand": "BP"},  # blocked by Render IP
    {"url": "https://fuelprices.esso.co.uk/latestdata.json",                                    "brand": "Esso"},
    {"url": "https://www.morrisons.com/fuel-prices/fuel.json",                                  "brand": "Morrisons"},
    # {"url": "https://jetlocal.co.uk/fuel_prices_data.json",                                     "brand": "Jet"},  # blocked by Render IP
    {"url": "https://fuel.motorfuelgroup.com/fuel_prices_data.json",                            "brand": "Motor Fuel Group"},
    {"url": "https://fuelprices.asconagroup.co.uk/newfuel.json",                                "brand": "Ascona"},
    {"url": "https://applegreenstores.com/fuel-prices/data.json",                               "brand": "Applegreen"},
    {"url": "https://www.rontec-servicestations.co.uk/fuel-prices/data/fuel_prices_data.json",  "brand": "Rontec"},
    {"url": "https://moto-way.com/fuel-price/fuel_prices.json",                                 "brand": "Moto"},
    # {"url": "https://www.tesco.com/fuel_prices/fuel_prices_data.json",                          "brand": "Tesco"},  # blocked by Render IP
    {"url": "https://api.sainsburys.co.uk/v1/exports/latest/fuel_prices_data.json",             "brand": "Sainsbury's"},
    {"url": "https://www.sgnretail.uk/files/data/SGN_daily_fuel_prices.json",                   "brand": "SGN"},
    {"url": "https://devapi.krlpos.com/integration/live_price/krl",                             "brand": "Karan Retail", "verify_ssl": False},
]

# Caches
_gov_cache    = {"stations": None, "fetched_at": 0}
_retail_cache = {"stations": None, "fetched_at": 0}
_token_cache  = {"token": None, "expires_at": 0}
GOV_CACHE_TTL    = 3600  # 60 mins
RETAIL_CACHE_TTL = 3600  # 60 mins - reduces blocking fetches


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_gov_token():
    """Get OAuth2 token from Government Fuel Finder API."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 30:
        return _token_cache["token"]
    try:
        resp = requests.post(
            GOV_TOKEN_URL,
            json={
                "client_id":     GOV_CLIENT_ID,
                "client_secret": GOV_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=5,
        )
        app.logger.info(f"GOV TOKEN STATUS: {resp.status_code}")
        app.logger.info(f"GOV TOKEN RESPONSE: {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
        # Response wrapped: {"success": true, "data": {"access_token": "...", "expires_in": 3600}}
        token_data = data.get("data", data)
        _token_cache["token"]      = token_data["access_token"]
        _token_cache["expires_at"] = now + token_data.get("expires_in", 3600)
        return _token_cache["token"]
    except Exception as e:
        app.logger.error(f"GOV TOKEN ERROR: {e}")
        return None


def fetch_gov_page(url, token, batch):
    """Fetch a single batch page from the Government API."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"batch-number": batch},
        timeout=5,  # Short timeout per page
    )
    resp.raise_for_status()
    return resp.json()


def fetch_gov_stations():
    """Fetch all stations from the Government Fuel Finder API."""
    now = time.time()
    if _gov_cache["stations"] and now - _gov_cache["fetched_at"] < GOV_CACHE_TTL:
        return _gov_cache["stations"]

    token = get_gov_token()
    if not token:
        return None

    fetch_start = time.time()
    MAX_FETCH_SECONDS = 5  # Gov API is currently blocked - fail fast

    # Step 1: Fetch station metadata (lat/lon/address) from PFS info endpoint
    station_meta = {}
    batch = 1
    while True:
        if time.time() - fetch_start > MAX_FETCH_SECONDS:
            break
        try:
            data  = fetch_gov_page(GOV_STATIONS_URL, token, batch)
            items = data if isinstance(data, list) else data.get("data", [])
            if not items:
                break
            for s in items:
                sid = str(s.get("node_id") or s.get("id") or "")
                if not sid:
                    continue
                # Skip permanently closed stations
                if s.get("permanent_closure"):
                    continue
                loc = s.get("location") or {}
                try:
                    lat = float(loc.get("latitude") or 0)
                    lon = float(loc.get("longitude") or 0)
                except (TypeError, ValueError):
                    continue
                if lat == 0 or lon == 0:
                    continue
                address = loc.get("address_line_1") or ""
                if loc.get("postcode"):
                    address = f"{address}, {loc.get('postcode')}".strip(", ")
                station_meta[sid] = {
                    "name":      s.get("trading_name") or s.get("brand_name") or "Unknown",
                    "brand":     s.get("brand_name") or s.get("trading_name") or "Unknown",
                    "address":   address,
                    "latitude":  lat,
                    "longitude": lon,
                    "e10": None, "e5": None, "b7": None,
                }
            if len(items) < 500:
                break
            batch += 1
        except Exception:
            break

    if not station_meta:
        app.logger.error("GOV API: No station metadata fetched")
        return None

    app.logger.info(f"GOV API: Fetched {len(station_meta)} stations")

    # Step 2: Fetch prices — each item contains node_id, trading_name, fuel_prices[]
    # fuel_prices: [{"fuel_type": "E10", "price": 132.9, ...}, ...]
    batch = 1
    prices_found = 0
    while True:
        if time.time() - fetch_start > MAX_FETCH_SECONDS:
            break
        try:
            data  = fetch_gov_page(GOV_PRICES_URL, token, batch)
            items = data if isinstance(data, list) else data.get("data", [])
            if not items:
                break
            for station in items:
                sid = str(station.get("node_id") or station.get("id") or "")
                if sid not in station_meta:
                    continue
                fuel_prices = station.get("fuel_prices", [])
                for fp in fuel_prices:
                    fuel = str(fp.get("fuel_type") or "").upper()
                    try:
                        price = float(fp.get("price") or 0)
                        # Prices are already in pence (e.g. 132.9)
                        if price < 10:
                            price *= 100
                        if not (50 <= price <= 500):
                            continue
                        price = round(price, 1)
                    except (TypeError, ValueError):
                        continue
                    if fuel == "E10":
                        station_meta[sid]["e10"] = price
                        prices_found += 1
                    elif fuel == "E5":
                        station_meta[sid]["e5"] = price
                        prices_found += 1
                    elif fuel in ("B7", "B7_STANDARD"):
                        station_meta[sid]["b7"] = price
                        prices_found += 1
            if len(items) < 500:
                break
            batch += 1
        except Exception:
            break

    app.logger.info(f"GOV API: Fetched {prices_found} prices")

    stations = [s for s in station_meta.values() if any([s["e10"], s["e5"], s["b7"]])]
    app.logger.info(f"GOV API: {len(stations)} stations with prices")

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
            verify_ssl = feed.get("verify_ssl", True)
            resp = requests.get(feed["url"], timeout=8, verify=verify_ssl)
            resp.raise_for_status()
            parsed = parse_feed(resp.json(), feed["brand"])
            app.logger.info(f"FEED OK {feed['brand']}: {len(parsed)} stations")
            all_stations.extend(parsed)
        except Exception as e:
            app.logger.error(f"FEED ERR {feed['brand']}: {e}")
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

@app.route("/robots.txt")
def robots_txt():
    return "User-agent: *\nAllow: /\nSitemap: https://fuelsaver.org.uk/sitemap.xml\n", 200, {"Content-Type": "text/plain"}


@app.route("/sitemap.xml")
def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://fuelsaver.org.uk/</loc>
    <changefreq>hourly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return xml, 200, {"Content-Type": "application/xml"}


@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
@app.route("/apple-touch-icon-120x120.png")
@app.route("/apple-touch-icon-120x120-precomposed.png")
def apple_touch_icon():
    import base64
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAIAAACyr5FlAAAC2klEQVR42u3dTW6DMBAG0Jynh+kBesLe0t22C5JCjD0/79MsI6GYp1EgHng85Hc+P6xBy7M+t4QGVmhghQlKmKCEiZwlWCDCBCVYIIIFIlggQgYfWCCCBSJkqO4+nG9EyOADC0TI4IMMPsjggww+sECEDJXdh1PFBxl8kMEHGXyQwQcZfMChQuNwGvgggw8y+CCDDzLUch/WGg4y+CCDDzgUGWq5D8vKBxxwkMEHHOpeHJaSDzL4gENNwWH5+IADDjLULB9WDQ4yFByqIo4RNS1xMNFQSSIcI1t64NA2NA844EiJY+RMdRzaRvMrFzji4AgHDg44zuNw1wuOQx9wwAEHHCdx+DMFjkMfqXA8vr/WFxzRcWxh8R8icMABR1Qc22U88VEXR7CbuJFxtOgcf3zAAQcccMABBxxw3I4j3q4COELsEYFj7n3SUhuI4IADDjjggAMOOOCAAw444IDj+teBoxWO04eGowmOi0eHo8Xt8/gy4IDjKY6Q47z1twnqHDYYwwEHHMGGmlYPtM0daoLDOORMHC5lG83K6hxwvPAx8WNwFJuyD/98MDjggAMOOOCAAw444IADjpg48szKwmGQGg444IADDjjgqIrDE4zhyP6oSTjggAMOOOCAIz8O71uB41AGHHDAAcclHN4OCYdXh8IBBxxzcXiXfWccrwMHHHDAcR5HAB9wRJUBBxyRfcARWAYccMABxyUcW33AEVsGHHDE9DFyppMMzaND53grcGgbEXCMKmmDY4mPUTENZCzxAUdaGTfjGHXTA8edPshILgMOOLb4gCO/jNt8wFFCBhxwLPbhaqWKDD7IgAOOcD8+Cmzwj/gtNqTY8/Oq1rZYejL4IIMPMuCAgw8y+FDBZfBBBh9k8EEGH2TwQQYiWPChisjggww+yOCDDESw4IMMRLDggwxEsEAEC+GDDESwQAQLRLCghAlEsKCECUqYkMBKhBUaWGmq4QeqkN94bmqEbwAAAABJRU5ErkJggg=="
    png_data = base64.b64decode(png_b64)
    return png_data, 200, {"Content-Type": "image/png"}


@app.route("/favicon.svg")
def favicon():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
  <circle cx="16" cy="16" r="16" fill="#004e2a"/>
  <rect x="8" y="10" width="10" height="14" rx="1" fill="white"/>
  <rect x="9.5" y="12" width="7" height="4" rx="0.5" fill="#00a85a"/>
  <rect x="7" y="22" width="12" height="2" rx="0.5" fill="white"/>
  <rect x="18" y="13" width="5" height="1.5" rx="0.5" fill="white"/>
  <rect x="21.5" y="11" width="1.5" height="4" rx="0.5" fill="white"/>
  <rect x="21" y="14.5" width="2.5" height="1.5" rx="0.5" fill="#00a85a"/>
</svg>'''
    return svg, 200, {"Content-Type": "image/svg+xml"}


@app.route("/ads.txt")
def ads_txt():
    content = (
        "google.com, pub-7146582862091752, DIRECT, f08c47fec0942fa0\n"
        "google-site-verification=wlbGa_5o-qFz2NWO1B95VulVMI4uPQD_JMuS5YrTVxI\n"
    )
    return content, 200, {"Content-Type": "text/plain"}

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


# Start background cache refresh thread
_cache_thread = threading.Thread(target=_background_cache_refresh, daemon=True)
_cache_thread.start()

if __name__ == "__main__":
    app.run(debug=True)
