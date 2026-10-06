#!/usr/bin/env python3
"""Build Malawi roads + railways + tourism from an OSM PBF file.

Usage:
    python scripts/build_data.py malawi-latest.osm.pbf
    python scripts/build_data.py /tmp/mw.osm.pbf --out data
"""
import json, math, os, sys, argparse, datetime
from collections import defaultdict

try:
    import osmium
except ImportError:
    sys.exit("Run: pip install osmium")


HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "track", "service", "path",
}
NORMALIZE = {
    "living_street": "residential", "road": "unclassified",
    "footway": "path", "cycleway": "path", "pedestrian": "path",
    "steps": "path", "bridleway": "path",
}
RAILWAY = {"rail", "narrow_gauge", "light_rail", "subway", "tram"}
PAVED = set("asphalt paved concrete paving_stones sett concrete:plates "
            "concrete:lanes bricks cobblestone chipseal metal".split())

PARK_BOUNDARY = {"national_park", "protected_area"}
NATURAL_TOURISM = {"beach", "bay"}
TOURISM_POI = {
    "attraction", "viewpoint", "artwork", "hotel", "guest_house",
    "camp_site", "hostel", "museum", "gallery", "information",
    "zoo", "picnic_site", "theme_park", "alpine_hut", "wilderness_hut",
    "chalet", "apartment", "caravan_site", "motel",
}


def km(coords):
    d = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        p = math.pi / 180
        h = (math.sin((y2 - y1) * p / 2) ** 2 +
             math.cos(y1 * p) * math.cos(y2 * p) *
             math.sin((x2 - x1) * p / 2) ** 2)
        d += 12742 * math.asin(math.sqrt(h))
    return round(d, 3)


def num(v):
    try: return int(str(v).split()[0])
    except Exception: return None


def tags_of(o):
    return {t.k: t.v for t in o.tags}


def road_feature(w, tags, coords):
    hw = tags.get("highway", "")
    cls = NORMALIZE.get(hw, hw.replace("_link", ""))
    if cls not in HIGHWAYS:
        return None
    su = tags.get("surface", "")
    tt = tags.get("tracktype", "")
    tg = "na"
    if cls == "track":
        tg = "g" + tt[5:] if tt[:5] == "grade" and tt[5:] in "12345" else "none"
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "id": w.id, "cls": cls, "hw": hw, "tg": tg,
            "name": tags.get("name", ""), "ref": tags.get("ref", ""),
            "surface": su,
            "sc": "unknown" if not su else "paved" if su in PAVED else "unpaved",
            "speed": num(tags.get("maxspeed")),
            "lit": "unk" if "lit" not in tags else ("no" if tags["lit"] == "no" else "yes"),
            "km": km(coords),
        },
    }


def rail_feature(w, tags, coords):
    rt = tags.get("railway", "")
    if rt not in RAILWAY:
        return None
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "id": w.id, "rt": rt,
            "name": tags.get("name", ""), "ref": tags.get("ref", ""),
            "usage": tags.get("usage", ""),
            "gauge": tags.get("gauge", ""),
            "electrified": tags.get("electrified", "no"),
            "km": km(coords),
        },
    }


def tourism_kind(tags):
    b = tags.get("boundary")
    if b in PARK_BOUNDARY:
        return ("park", b)
    nat = tags.get("natural")
    if nat in NATURAL_TOURISM:
        return ("natural", nat)
    tou = tags.get("tourism")
    if tou in TOURISM_POI:
        return ("poi", tou)
    return None


def tourism_props(o, tags, kind_cat):
    kind, cat = kind_cat
    p = {"id": o.id, "kind": kind, "cat": cat, "name": tags.get("name", "")}
    for k in ("ele", "website", "phone", "opening_hours",
              "description", "wikipedia", "operator"):
        if k in tags:
            p[k] = tags[k]
    return p


class Extract(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.roads = []
        self.railways = []
        self.tourism_pts = []
        self.tourism_areas = []

    def node(self, n):
        if not n.tags:
            return
        tags = tags_of(n)
        kc = tourism_kind(tags)
        if not kc:
            return
        self.tourism_pts.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(n.location.lon, 5),
                                         round(n.location.lat, 5)]},
            "properties": tourism_props(n, tags, kc),
        })

    def way(self, w):
        if not w.tags:
            return
        tags = tags_of(w)
        try:
            coords = [[round(n.lon, 5), round(n.lat, 5)] for n in w.nodes]
        except Exception:
            return
        if len(coords) < 2:
            return

        r = road_feature(w, tags, coords)
        if r:
            self.roads.append(r)
            return

        r = rail_feature(w, tags, coords)
        if r:
            self.railways.append(r)
            return

        kc = tourism_kind(tags)
        if kc and len(coords) >= 4 and coords[0] == coords[-1]:
            self.tourism_areas.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": tourism_props(w, tags, kc),
            })


def write_geojson(path, feats):
    with open(path, "w") as fh:
        fh.write('{"type":"FeatureCollection","features":[\n' +
                 ",\n".join(json.dumps(f, separators=(",", ":")) for f in feats) +
                 "\n]}")


def count_by(feats, key):
    out = defaultdict(int)
    for f in feats:
        out[key(f)] += 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf", help="path to .osm.pbf file")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    if not os.path.exists(args.pbf):
        sys.exit(f"PBF not found: {args.pbf}")
    os.makedirs(args.out, exist_ok=True)

    print(f"Reading {args.pbf} …")
    ex = Extract()
    ex.apply_file(args.pbf, locations=True)
    print(f"  roads:          {len(ex.roads):,}")
    print(f"  railways:       {len(ex.railways):,}")
    print(f"  tourism points: {len(ex.tourism_pts):,}")
    print(f"  tourism areas:  {len(ex.tourism_areas):,}")

    GRID = 0.5
    cells = defaultdict(list)
    for r in ex.roads:
        x, y = r["geometry"]["coordinates"][0]
        cells[(math.floor(x / GRID), math.floor(y / GRID))].append(r)

    roads_dir = os.path.join(args.out, "roads")
    os.makedirs(roads_dir, exist_ok=True)
    regions = []
    for (cx, cy), fs in cells.items():
        name = f"{cx}_{cy}.geojson"
        write_geojson(os.path.join(roads_dir, name), fs)
        regions.append({
            "file": f"roads/{name}",
            "bbox": [cy * GRID, cx * GRID, (cy + 1) * GRID, (cx + 1) * GRID],
            "count": len(fs),
        })
    print(f"  → {len(regions)} road cells")

    write_geojson(os.path.join(args.out, "railways.geojson"), ex.railways)

    tourism = ex.tourism_pts + ex.tourism_areas
    write_geojson(os.path.join(args.out, "tourism.geojson"), tourism)

    stats = {
        "roads": len(ex.roads),
        "roads_km": round(sum(r["properties"]["km"] for r in ex.roads), 1),
        "railways": len(ex.railways),
        "railways_km": round(sum(r["properties"]["km"] for r in ex.railways), 1),
        "tourism": len(tourism),
        "tourism_by_cat": count_by(tourism, lambda f: f["properties"]["cat"]),
        "tourism_by_kind": count_by(tourism, lambda f: f["properties"]["kind"]),
    }
    json.dump(stats, open(os.path.join(args.out, "stats.json"), "w"), indent=1)

    meta = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        "regions": regions,
        "single_files": ["railways.geojson", "tourism.geojson"],
    }
    json.dump(meta, open(os.path.join(args.out, "meta.json"), "w"), indent=1)

    print("Done.")


if __name__ == "__main__":
    main()