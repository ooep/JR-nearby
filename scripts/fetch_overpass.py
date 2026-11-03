#!/usr/bin/env python3
"""Fetch POI candidates around a station from the Overpass API (OpenStreetMap).

Usage:
  python3 scripts/fetch_overpass.py --lat 35.680841 --lon 139.766931 \
      [--radius 1200] [--out /tmp/candidates.json]

Outputs JSON list of candidates with tags + distance_m, sorted by distance.
Candidates are RAW material: every POI shipped in data/ still needs a
verified official/map URL and hand-written trilingual descriptions.
"""
import argparse
import json
import math
import subprocess
import sys

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "JR-nearby-research/1.0 (contact: ooep@users.noreply.github.com)"

# tag selectors: tourism / worship / food / cafe / shopping / bath / park / fun / lodging / public service
SELECTORS = [
    '["tourism"~"attraction|museum|viewpoint|artwork|gallery|zoo|theme_park|aquarium"]',
    '["historic"~"monument|memorial|castle|ruins|heritage|temple|shrine|church"]',
    '["amenity"="place_of_worship"]["religion"~"shinto|buddhist"]',
    '["amenity"~"restaurant|cafe|fast_food|food_court|izakaya|bar|pub|marketplace"]',
    '["shop"~"department_store|mall|supermarket|convenience|shopping_centre|souvenir|bakery|confectionery"]',
    '["amenity"~"public_bath|community_centre"]',
    '["leisure"~"park|garden|playground|sports_centre"]',
    '["amenity"~"townhall|post_office|hospital|clinic|pharmacy|bank|police|library"]',
    '["tourism"="information"]["information"="office"]',
    '["amenity"="atm"]',
]


def haversine(lat1, lon1, lat2, lon2):
    la1, lo1, la2, lo2 = map(math.radians, (lat1, lon1, lat2, lon2))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def build_query(lat, lon, radius):
    parts = []
    for sel in SELECTORS:
        parts.append(f"nwr{sel}(around:{radius},{lat},{lon});")
    return ("[out:json][timeout:60];(" + "".join(parts) + ");out tags center 400;")


def fetch(lat, lon, radius):
    q = build_query(lat, lon, radius)
    last_err = None
    for ep in ENDPOINTS:
        try:
            p = subprocess.run(
                ["curl", "-s", "--max-time", "90", "-A", UA,
                 "--data-urlencode", f"data={q}", ep],
                capture_output=True)
            return json.loads(p.stdout.decode())
        except Exception as e:
            last_err = e
            print(f"[overpass] {ep} failed: {e}", file=sys.stderr)
    raise SystemExit(f"all overpass endpoints failed: {last_err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--radius", type=int, default=1200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    d = fetch(args.lat, args.lon, args.radius)
    out = []
    for e in d.get("elements", []):
        t = e.get("tags") or {}
        if not t.get("name"):
            continue
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        out.append({
            "osm_type": e["type"], "osm_id": e["id"],
            "name": t.get("name"), "name_en": t.get("name:en"), "name_zh": t.get("name:zh"),
            "tags": {k: v for k, v in t.items() if k in (
                "tourism", "historic", "amenity", "shop", "leisure", "religion", "denomination",
                "cuisine", "website", "contact:website", "opening_hours", "phone", "contact:phone",
                "wikidata", "wikipedia", "fee", "wheelchair")},
            "lat": lat, "lon": lon,
            "distance_m": round(haversine(args.lat, args.lon, lat, lon)),
            "osm_url": f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
        })
    out.sort(key=lambda x: x["distance_m"])
    js = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(js)
        print(f"[ok] {len(out)} candidates -> {args.out}")
    else:
        print(js)


if __name__ == "__main__":
    main()
