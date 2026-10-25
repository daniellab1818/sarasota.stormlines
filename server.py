# flask setup
from flask import Flask, request, jsonify, send_from_directory
import requests

app = Flask(__name__)


# how far the address is from the nearest water (census hydro layer)
def distance_to_water(lat, lon):
    url = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Hydro/MapServer/1/query"
    rings = [30, 60, 120, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000]

    # check bigger and bigger rings until we hit water
    for d in rings:
        params = {
            "where": "1=1",
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "distance": d,
            "units": "esriSRUnit_Meter",
            "spatialRel": "esriSpatialRelIntersects",
            "returnCountOnly": "true",
            "f": "json"
        }
        r = requests.get(url, params=params).json()
        if r.get("count", 0) > 0:
            return round(d / 1609.34, 1)

    return round(32000 / 1609.34, 1)


# rough surge guess from the category (backup when NHC has nothing)
def estimate_surge(category):
    bands = {5: 18, 4: 13, 3: 9, 2: 6, 1: 4}
    return bands.get(category, 1)


# real surge band from the NHC peak surge layer (only works during a storm)
def nhc_surge_band(lat, lon):
    url = "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_PeakStormSurge/MapServer/2/query"
    params = {
        "where": "1=1",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json"
    }
    r = requests.get(url, params=params, timeout=10).json()
    feats = r.get("features", [])
    if not feats:
        return None

    label = str(feats[0]["attributes"].get("name", "")).lower()
    if "above 12" in label:
        return 14
    if "12 ft" in label:
        return 12
    if "9 ft" in label:
        return 9
    if "6 ft" in label:
        return 6
    if "3 ft" in label:
        return 3
    return None


# ---------- pages ----------

@app.route('/')
def home():
    return send_from_directory('.', 'congressionalappchallenge.html')


@app.route('/<path:filename>')
def serve_file(filename):
    return send_from_directory('.', filename)


# ---------- data routes ----------

# address -> elevation, evac zone, distance to water
@app.route('/elevation')
def elevation():
    address = request.args.get('address')

    geo = requests.get(
        "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
        params={"address": address, "benchmark": "Public_AR_Current", "format": "json"}
    ).json()

    matches = geo["result"]["addressMatches"]
    if len(matches) == 0:
        return jsonify({"error": "address not found"})

    coords = matches[0]["coordinates"]
    lon = coords["x"]
    lat = coords["y"]

    elev = requests.get(
        "https://epqs.nationalmap.gov/v1/json",
        params={"x": lon, "y": lat, "units": "Feet", "wkid": 4326, "includeDate": "false"}
    ).json()

    evac = requests.get(
        "https://ags3.scgov.net/server/rest/services/Hosted/StormEvacuationZone/FeatureServer/0/query",
        params={
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json"
        }
    ).json()

    if "value" not in elev:
        return jsonify({"error": "elevation temporarily unavailable, try again"})

    if len(evac["features"]) > 0:
        zone = evac["features"][0]["attributes"]["evacuationzone"]
    else:
        zone = "Not in an evacuation zone"

    water_dist = distance_to_water(lat, lon)

    return jsonify({
        "elevation": elev["value"],
        "lat": lat,
        "lon": lon,
        "zone": zone,
        "distanceInland": water_dist
    })


# surge for a location + category (real NHC, falls back to an estimate)
@app.route('/surge')
def surge():
    lat = float(request.args.get('lat'))
    lon = float(request.args.get('lon'))
    cat = int(request.args.get('cat'))

    try:
        band = nhc_surge_band(lat, lon)
    except Exception:
        band = None

    if band is not None:
        return jsonify({"surge": band, "source": "nhc"})

    return jsonify({"surge": estimate_surge(cat), "source": "estimate"})


# current atlantic storm from the NHC feed
@app.route('/storm')
def storm():
    data = requests.get("https://www.nhc.noaa.gov/CurrentStorms.json").json()
    storms = data["activeStorms"]

    # only want atlantic storms (id starts with "al")
    atlanticStorms = []
    for s in storms:
        if s["id"].startswith("al"):
            atlanticStorms.append(s)

    if len(atlanticStorms) == 0:
        return jsonify({"active": False})

    storm = atlanticStorms[0]
    return jsonify({
        "active": True,
        "name": storm["name"],
        "classification": storm["classification"],
        "windSpeed": storm["intensity"],
        "pressure": storm["pressure"],
        "lat": storm["latitudeNumeric"],
        "lon": storm["longitudeNumeric"],
        "movementDir": storm["movementDir"],
        "movementSpeed": storm["movementSpeed"]
    })


if __name__ == '__main__':
    app.run(port=8000)