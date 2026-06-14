"""Build real per-zone polygons for the gauge-fed oyster bays (Phase 1).

Replaces the coarse axis-aligned bbox rectangles in seed_areas.py for the six
gauge-fed bays with REAL water-body polygons (OpenStreetMap), clipped along the
river->mouth salinity gradient into upper/mid/lower (or quadrant) zones per the
adversarially-verified zone spec.

Pipeline (deterministic, cached to disk so re-runs are offline-repeatable):
  fetch real water geometry (Overpass)  ->  shapely assemble + make_valid
  ->  simplify  ->  clip each zone by its lat/lon cut box  ->  coerce to a
  single valid Polygon (largest part; area dropped is logged)  ->  emit GeoJSON.

Output: zone_geoms.json — a manifest {slug: {name, region, linked_gauges,
description, geometry}} consumed by seed_areas.py. Geometry is a GeoJSON Polygon
fed straight into the existing ST_GeomFromGeoJSON upsert (which re-validates and
forces CCW), so no schema change: each zone is just another 'predefined' row.

Galveston and Albemarle have no clean OSM polygon and are built separately from
heavier sources (FOSSGIS / NHD) — they are NOT in CLEAN_BAYS here.

Usage:
  python build_zone_geoms.py            # build all CLEAN_BAYS
  python build_zone_geoms.py mobile-bay # build one bay
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping, shape
from shapely.ops import linemerge, polygonize, unary_union

try:
    from shapely.validation import make_valid
except ImportError:  # very old shapely
    def make_valid(g):
        return g.buffer(0)

HERE = Path(__file__).parent
CACHE = HERE / ".cache"
CACHE.mkdir(exist_ok=True)
OUT = HERE / "zone_geoms.json"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

SIMPLIFY_TOL = 0.001          # ~100 m; estuary-scale, keeps map light
MAX_VERTICES = 500            # round-trip-safe through the validated area path
KEEP_PART_FRAC = 0.15         # warn if a zone drops > this fraction of its area
# For marsh estuaries fetched as "all water in bbox" (Barataria): clip to the
# requested box and drop water bodies smaller than this (deg^2, ~5 km^2) so the
# thousands of marsh ponds + bayou slivers don't drown the main bay + lakes.
MIN_WATER_AREA = 0.0005

# ---------------------------------------------------------------------------
# Zone spec (from the adversarially-verified consolidated spec). cut = clip box
# [W, S, E, N]; the boxes tile each bay's extent without overlap. linked_gauges
# only where the gauge is a defensible freshwater driver for that zone.
# ---------------------------------------------------------------------------
BAYS: dict = {
    "barataria-bay": {
        "region": "gulf",
        "fetch": {"kind": "nhd_bbox", "bbox": (29.20, -90.50, 30.10, -89.60)},
        "zones": [
            ("barataria-bay-upper", "Barataria Bay — Upper", [-90.50, 29.45, -89.60, 30.10],
             ["07374000", "07381490"],
             "Upper Barataria Basin: fresh-to-brackish inner sub-bays and the Little "
             "Lake / Lake Salvador throat. Freshwater enters at the top via the Davis "
             "Pond Mississippi diversion; 07374000 (Mississippi) is the primary damped "
             "proxy, 07381490 (Atchafalaya, GIWW) a weaker secondary one."),
            ("barataria-bay-lower", "Barataria Bay — Lower", [-90.50, 29.20, -89.60, 29.45],
             [],
             "Lower Barataria Bay: saline open bay and the tidal passes by Grand Isle. "
             "Marine-exchange dominated — no upstream gauge applies; salinity fidelity "
             "comes from NGOFS2 where available."),
        ],
    },
    "mobile-bay": {
        "region": "gulf",
        "fetch": {"kind": "relation", "id": 10736849},
        "zones": [
            ("mobile-bay-upper", "Mobile Bay — Upper", [-88.20, 30.50, -87.70, 30.72],
             ["02469761", "02428400"],
             "Upper Mobile Bay: fresh delta head. Driven by the sum of the Tombigbee "
             "(02469761) and Alabama (02428400) arms, ~93% of Mobile-Tensaw delta inflow."),
            ("mobile-bay-mid", "Mobile Bay — Mid", [-88.20, 30.32, -87.70, 30.50],
             ["02469761", "02428400"],
             "Mid Mobile Bay: brackish transition. Same two-arm delta inflow, muted and "
             "lagged by mid-bay (equal-weighted in v1; NGOFS2 is the better fidelity layer)."),
            ("mobile-bay-lower", "Mobile Bay — Lower", [-88.20, 30.20, -87.70, 30.32],
             [],
             "Lower Mobile Bay incl. the Bon Secour lobe: saline, tide/Gulf-exchange "
             "dominated at Main Pass — no upstream gauge applies; NGOFS2 salinity."),
        ],
    },
    "chesapeake-bay": {
        "region": "east_coast",
        "fetch": {"kind": "relation", "id": 11884052},
        "zones": [
            ("chesapeake-bay-upper", "Chesapeake Bay — Upper", [-77.40, 38.99, -75.70, 39.70],
             ["01578310"],
             "Upper Chesapeake: oligohaline, Susquehanna head to the Bay Bridge. "
             "01578310 (Susquehanna @ Conowingo, ~50% of total bay freshwater) is an "
             "excellent direct driver."),
            ("chesapeake-bay-mid", "Chesapeake Bay — Mid", [-77.40, 37.60, -75.70, 38.99],
             ["01578310", "01646500"],
             "Mid Chesapeake: mesohaline, Bay Bridge to the Rappahannock mouth. "
             "Susquehanna as a lagged basin proxy plus the Potomac (01646500), the "
             "2nd-largest tributary entering this band (~38.0N). CBOFS is the fidelity layer."),
            ("chesapeake-bay-lower", "Chesapeake Bay — Lower", [-77.40, 36.90, -75.70, 37.60],
             ["02037500"],
             "Lower Chesapeake: polyhaline, Rappahannock mouth to the Atlantic. "
             "02037500 (James) is a secondary, ocean-modulated signal — the lower bay "
             "is ocean-exchange dominated."),
        ],
    },
    "pamlico-sound": {
        "region": "east_coast",
        "fetch": {"kind": "relation", "id": 11190230},
        "zones": [
            ("pamlico-sound-southwest", "Pamlico Sound — Southwest (Neuse)", [-76.70, 34.90, -75.95, 35.30],
             ["02089000"],
             "SW Pamlico Sound, Neuse-driven fresh end. 02089000 (Neuse @ Kinston) is a "
             "genuine but partial driver (~53% of direct sound inflow) dominating this lobe."),
            ("pamlico-sound-northwest", "Pamlico Sound — Northwest (Tar-Pamlico)", [-76.70, 35.30, -75.95, 35.85],
             ["02083500"],
             "NW Pamlico Sound, Tar-Pamlico-driven. 02083500 (Tar @ Tarboro) is the lowest "
             "non-tidal Tar gauge, added to give this lobe a real freshwater signal."),
            ("pamlico-sound-north", "Pamlico Sound — North (Albemarle outflow)", [-75.95, 35.55, -75.40, 35.85],
             ["02080500"],
             "Northern Pamlico Sound: influenced by Albemarle Sound outflow (the largest "
             "single freshwater influence) entering via Croatan/Roanoke sounds. 02080500 "
             "(Roanoke) is the available lagged proxy."),
            ("pamlico-sound-east", "Pamlico Sound — East", [-75.95, 34.90, -75.40, 35.55],
             [],
             "Eastern Pamlico Sound: inlet/wind-dominated ocean end behind the Outer Banks "
             "(Hatteras/Ocracoke inlets) — no river gauge applies. Wind-dominated, so no "
             "OFS in-bay salinity; directional/discharge-proxy coverage only."),
        ],
    },
    # NOTE: galveston-bay and albemarle-sound were zoned here but REVERTED to single
    # bounding boxes in seed_areas.py — CMEMS (~8 km) only reached their outer/oceanic
    # zone, so the inner zones went blank (inconsistent within-bay data). A single bbox
    # samples the covered cells and gives one consistent salinity per bay.
}

# Bays with a clean single OSM water relation. Barataria turns out NOT to be one
# (open lower bay is coastline-defined, not a natural=water polygon; upper basin
# is dozens of separate marsh lakes), so it moves to the coastline-source bucket
# alongside Galveston and Albemarle — see build_zone_geoms_coastline.py (Phase 1b).
CLEAN_BAYS = ["mobile-bay", "chesapeake-bay", "pamlico-sound"]


# ---------------------------------------------------------------------------
# Overpass fetch (cached)
# ---------------------------------------------------------------------------

def _overpass(query: str, cache_key: str) -> dict:
    cache_file = CACHE / f"{cache_key}.json"
    if cache_file.exists():
        print(f"  cache hit: {cache_file.name}")
        return json.loads(cache_file.read_text(encoding="utf-8"))
    last_err = None
    for ep in OVERPASS_ENDPOINTS:
        try:
            print(f"  fetching from {ep} ...")
            r = httpx.post(ep, data={"data": query}, timeout=300,
                           headers={"User-Agent": "OysterHealth/1.0 (zone geometry seed)"})
            r.raise_for_status()
            data = r.json()
            cache_file.write_text(json.dumps(data), encoding="utf-8")
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  endpoint failed ({e}); trying next ...")
            time.sleep(2)
    raise RuntimeError(f"all Overpass endpoints failed: {last_err}")


def _lines_from_element(el: dict) -> list[LineString]:
    lines = []
    if el.get("type") == "way" and el.get("geometry"):
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) >= 2:
            lines.append(LineString(coords))
    elif el.get("type") == "relation":
        for m in el.get("members", []):
            if m.get("type") == "way" and m.get("geometry"):
                coords = [(p["lon"], p["lat"]) for p in m["geometry"]]
                if len(coords) >= 2:
                    lines.append(LineString(coords))
    return lines


def _polygon_from_elements(elements: list[dict]):
    """Assemble OSM ways/relations into one (Multi)Polygon water body."""
    closed, open_lines = [], []
    for el in elements:
        for ls in _lines_from_element(el):
            (closed if ls.is_ring else open_lines).append(ls)
    polys = [Polygon(ls) for ls in closed]
    if open_lines:
        merged = linemerge(open_lines)
        polys.extend(polygonize(merged))
    if not polys:
        return None
    cleaned = []
    for p in polys:
        if not p.is_valid:
            p = make_valid(p)
        if p.is_valid and not p.is_empty and p.area > 0:
            cleaned.append(p)
    if not cleaned:
        return None
    return make_valid(unary_union(cleaned))


# USGS NHD (national hydrography) — a coastline-derived source for the bays OSM
# has no clean relation for (Barataria/Galveston/Albemarle). Query BOTH:
#   layer 9  = Area      → big estuary polygons (Galveston Bay, Albemarle Sound)
#   layer 12 = Waterbody → bay/lake polygons (Barataria Bay + its upper lakes)
# Either can fail for a given bbox (layer 9 intermittently returns a non-JSON
# error; layer 12 hits a 2000-record cap of small ponds), so tolerate per-layer
# failure — between them the bay is always captured.
NHD_BASE = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer"
NHD_LAYERS = (9, 12)


def _nhd_polygon(slug: str, bbox: tuple):
    cache_file = CACHE / f"{slug}-nhd.geojson"
    if cache_file.exists():
        print(f"  cache hit: {cache_file.name}")
        feats = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        s, w, n, e = bbox
        params = {
            "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
            "outFields": "objectid", "returnGeometry": "true", "outSR": "4326", "f": "geojson",
        }
        feats = []
        for layer in NHD_LAYERS:
            try:
                r = httpx.get(f"{NHD_BASE}/{layer}/query", params=params, timeout=120)
                r.raise_for_status()
                lf = r.json().get("features", [])
                feats.extend(lf)
                print(f"  NHD layer {layer}: {len(lf)} feats")
            except Exception as e:  # noqa: BLE001
                print(f"  NHD layer {layer}: skipped ({e})")
        cache_file.write_text(json.dumps(feats), encoding="utf-8")
    polys = []
    for feat in feats:
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            sh = shape(geom)
        except Exception:  # noqa: BLE001
            continue
        if not sh.is_valid:
            sh = make_valid(sh)
        if sh.is_valid and not sh.is_empty and sh.area > 0:
            polys.append(sh)
    if not polys:
        return None
    return make_valid(unary_union(polys))


def fetch_bay_polygon(slug: str, fetch: dict):
    kind = fetch["kind"]
    if kind == "relation":
        rid = fetch["id"]
        q = f"[out:json][timeout:300];rel({rid});out geom;"
        data = _overpass(q, f"{slug}-rel-{rid}")
        poly = _polygon_from_elements(data.get("elements", []))
    elif kind == "water_bbox":
        s, w, n, e = fetch["bbox"]
        q = (f"[out:json][timeout:240];("
             f"way[natural=water]({s},{w},{n},{e});"
             f"relation[natural=water]({s},{w},{n},{e}););out geom;")
        data = _overpass(q, f"{slug}-water-bbox")
        poly = _polygon_from_elements(data.get("elements", []))
    elif kind == "nhd_bbox":
        poly = _nhd_polygon(slug, fetch["bbox"])
    else:
        raise ValueError(kind)

    if poly is None:
        raise RuntimeError(f"{slug}: no polygon assembled from source")

    # Bbox sources (OSM water + NHD) return whole features touching the box and a
    # cloud of small water bodies; clip to the box and keep only significant ones
    # so marsh ponds / the open Gulf don't dominate.
    if kind in ("water_bbox", "nhd_bbox"):
        s, w, n, e = fetch["bbox"]
        poly = make_valid(poly.intersection(box(w, s, e, n)))
        parts = [poly] if poly.geom_type == "Polygon" else list(getattr(poly, "geoms", []))
        big = [p for p in parts if p.geom_type == "Polygon" and p.area >= MIN_WATER_AREA]
        if not big:
            raise RuntimeError(f"{slug}: no water body >= MIN_WATER_AREA after bbox clip")
        print(f"  kept {len(big)} significant water bodies of {len(parts)} after bbox clip + area filter")
        poly = make_valid(unary_union(big))
    return poly


# ---------------------------------------------------------------------------
# Clip + coerce
# ---------------------------------------------------------------------------

def _largest_part(geom, slug: str):
    """Coerce to a single Polygon; log area dropped if multipart."""
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        geom = unary_union(polys) if polys else geom
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type != "MultiPolygon" or geom.is_empty:
        return geom
    parts = sorted(geom.geoms, key=lambda g: g.area, reverse=True)
    total = sum(p.area for p in parts)
    largest = parts[0]
    dropped = (total - largest.area) / total if total else 0.0
    if dropped > KEEP_PART_FRAC:
        print(f"  WARN {slug}: kept largest of {len(parts)} parts, dropped "
              f"{dropped*100:.1f}% of area — review (may amputate a real sub-bay)")
    elif len(parts) > 1:
        print(f"  {slug}: {len(parts)} parts, dropped {dropped*100:.2f}% (slivers)")
    return largest


def _count_vertices(poly: Polygon) -> int:
    return len(poly.exterior.coords) + sum(len(r.coords) for r in poly.interiors)


def build_zone(bay_poly, zone, slug: str) -> Polygon:
    zslug, _name, clip, _g, _d = zone
    clipped = bay_poly.intersection(box(clip[0], clip[1], clip[2], clip[3]))
    if clipped.is_empty:
        raise RuntimeError(f"{zslug}: clip produced empty geometry")
    if not clipped.is_valid:
        clipped = make_valid(clipped)
    # intersection may return GeometryCollection (polys + line slivers) — keep polys
    if clipped.geom_type == "GeometryCollection":
        polys = [g for g in clipped.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        clipped = unary_union(polys) if polys else clipped
    poly = _largest_part(clipped, zslug)
    poly = poly.simplify(SIMPLIFY_TOL, preserve_topology=True)
    tol = SIMPLIFY_TOL
    while _count_vertices(poly) > MAX_VERTICES and tol < 0.02:
        tol *= 1.7
        poly = poly.simplify(tol, preserve_topology=True)
    if not poly.is_valid:
        poly = make_valid(poly)
    # make_valid / simplify can split a polygon into a MultiPolygon or collection;
    # re-coerce to a single Polygon so it fits the POLYGON(4326) geom column.
    poly = _largest_part(poly, zslug)
    return poly


def main() -> None:
    targets = sys.argv[1:] or CLEAN_BAYS
    manifest = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    for slug in targets:
        if slug not in BAYS:
            print(f"SKIP unknown bay {slug}")
            continue
        spec = BAYS[slug]
        print(f"\n=== {slug} ===")
        bay_poly = fetch_bay_polygon(slug, spec["fetch"])
        bay_poly = bay_poly.simplify(SIMPLIFY_TOL, preserve_topology=True)
        bay_poly = bay_poly.buffer(0)   # clean topology (NHD shells/holes) before clipping
        print(f"  bay polygon: area~{bay_poly.area:.4f} deg^2, "
              f"bounds={tuple(round(b, 3) for b in bay_poly.bounds)}")
        for zone in spec["zones"]:
            zslug, name, _clip, gauges, desc = zone
            poly = build_zone(bay_poly, zone, slug)
            manifest[zslug] = {
                "name": name,
                "region": spec["region"],
                "linked_gauges": gauges,
                "description": desc,
                "geometry": mapping(poly),
            }
            print(f"  {zslug}: {_count_vertices(poly)} verts, "
                  f"area~{poly.area:.4f} deg^2, gauges={gauges}")
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(manifest)} zones to {OUT.name}")


if __name__ == "__main__":
    main()
