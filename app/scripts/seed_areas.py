"""Seed coastal areas as PostGIS polygons (idempotent — safe to re-run).

Two kinds of area, both stored as area_type='predefined' rows:
  - Bbox areas: simple [W, S, E, N] rectangles in the AREAS list below
    (minor / ungauged bays). Edit a box here and re-run.
  - Zoned bays: the gauge-fed oyster bays, split into salinity-gradient zones
    with REAL OSM/NHD geometry by build_zone_geoms.py -> zone_geoms.json, which
    this script loads. Edit those in build_zone_geoms.py, rebuild, then re-run.

This script is the single source of truth for which areas exist: it UPSERTs every
area in (AREAS + the manifest), then PRUNES any DB area whose slug is no longer
present — so removing an area is a one-step edit (delete it from AREAS or the
manifest and re-run). Pruning cascades to the area's indicators + snapshots, and
is SKIPPED when the manifest is missing, so a missing zone_geoms.json can never
wipe the zone areas. Slugs are the stable key — keep them stable when editing
geometry. All geometry passes through ST_MakeValid(ST_Force2D(ST_ForcePolygonCCW(...))).

See AREAS.md in this folder for the full edit workflow.
"""
import json
from pathlib import Path

from _db import conn

ZONE_MANIFEST = Path(__file__).parent / "zone_geoms.json"

# (slug, name, region, [west, south, east, north], description, linked_gauges)
# The 6 gauge-fed bays (mobile/chesapeake/pamlico/barataria/galveston/albemarle)
# are intentionally absent here — they come from the zone manifest as real-geometry
# zones. Anything removed from this list (or the manifest) is pruned from the DB on
# re-seed; see AREAS.md.
AREAS = [
    # --- Gulf of Mexico ---
    # NB: barataria-bay (gulf) is zoned from NHD geometry via the manifest (see
    # build_zone_geoms.py), alongside mobile/chesapeake/pamlico. galveston-bay and
    # albemarle-sound were tried as zones but REVERTED to single bboxes — CMEMS
    # (~8 km) couldn't resolve their sub-bay zones, so inner zones went blank.
    ("galveston-bay", "Galveston Bay", "gulf",
     [-95.10, 29.30, -94.65, 29.85],
     "Texas's primary oyster bay; Trinity + San Jacinto + Buffalo Bayou inflow.",
     ["08066500", "08068000", "08070000", "08074000"]),
    ("terrebonne-bay", "Terrebonne Bay", "gulf",
     [-90.95, 29.10, -90.50, 29.45],
     "South-central Louisiana bay system. Heavy shrimping.",
     ["07381490"]),
    ("breton-chandeleur-sound", "Breton / Chandeleur Sound", "gulf",
     [-89.40, 29.40, -88.60, 30.20],
     "East of the Mississippi delta, between the river and the Chandeleur Islands.",
     ["07374000"]),
    ("mississippi-sound", "Mississippi Sound", "gulf",
     [-89.30, 30.20, -88.20, 30.45],
     "Behind the barrier islands from LA to AL. Oysters, shrimp, blue crab.",
     ["07374000", "02469761"]),
    ("pensacola-bay", "Pensacola Bay", "gulf",
     [-87.40, 30.30, -86.95, 30.55],
     "Florida panhandle estuary.",
     []),
    ("apalachicola-bay", "Apalachicola Bay", "gulf",
     [-85.20, 29.60, -84.70, 29.85],
     "Florida oyster bay; sensitive to Apalachicola River discharge.",
     ["02358000"]),
    ("tampa-bay", "Tampa Bay", "gulf",
     [-82.80, 27.50, -82.35, 28.05],
     "Largest open-water estuary in Florida.",
     []),
    ("charlotte-harbor", "Charlotte Harbor", "gulf",
     [-82.30, 26.55, -81.85, 26.95],
     "SW Florida estuary fed by the Peace and Myakka rivers.",
     []),
    ("matagorda-bay", "Matagorda Bay", "gulf",
     [-96.70, 28.40, -96.10, 28.80],
     "Texas mid-coast bay.",
     []),
    ("corpus-christi-bay", "Corpus Christi Bay", "gulf",
     [-97.45, 27.65, -97.10, 27.90],
     "South Texas bay; hypersaline in drought.",
     []),
    ("sabine-lake", "Sabine Lake", "gulf",
     [-94.05, 29.65, -93.75, 30.05],
     "TX/LA border estuary fed by Sabine and Neches rivers.",
     ["08028500"]),
    ("lake-pontchartrain", "Lake Pontchartrain", "gulf",
     [-90.50, 30.10, -89.65, 30.40],
     "Brackish lake north of New Orleans. Connected to Mississippi Sound via the Rigolets.",
     ["07374000"]),
    ("marsh-island", "Marsh Island", "gulf",
     [-92.10, 29.42, -91.70, 29.68],
     "Louisiana marsh island and surrounding oyster grounds between Vermilion Bay "
     "and Atchafalaya Bay; freshwater dominated by the Atchafalaya River.",
     ["07381490"]),

    # --- East Coast ---
    ("delaware-bay", "Delaware Bay", "east_coast",
     [-75.50, 38.80, -74.85, 39.45],
     "DE/NJ estuary fed by the Delaware River.",
     []),
    ("albemarle-sound", "Albemarle Sound", "east_coast",
     [-76.80, 35.85, -75.65, 36.15],
     "NC's northern sound; fed by the Roanoke and Chowan rivers.",
     ["02080500"]),
    ("core-sound", "Core Sound", "east_coast",
     [-76.55, 34.60, -76.20, 34.95],
     "Narrow NC sound behind the Outer Banks.",
     []),
    ("st-helena-sound", "St. Helena Sound", "east_coast",
     [-80.55, 32.35, -80.25, 32.55],
     "SC sound at the mouth of the ACE Basin.",
     []),
    ("charleston-harbor", "Charleston Harbor", "east_coast",
     [-80.05, 32.65, -79.85, 32.85],
     "SC working harbor + estuary.",
     []),
    ("st-johns-river-estuary", "St. Johns River Estuary", "east_coast",
     [-81.80, 30.20, -81.35, 30.45],
     "Lower St. Johns River, north Florida.",
     []),
    ("indian-river-lagoon", "Indian River Lagoon", "east_coast",
     [-80.65, 27.20, -80.30, 28.55],
     "Long, narrow Florida east-coast lagoon system.",
     []),
    ("long-island-sound", "Long Island Sound", "east_coast",
     [-73.75, 40.90, -72.20, 41.30],
     "NY/CT sound; oysters, hard clams, scallops.",
     []),
]


def bbox_geojson(bbox: list[float]) -> str:
    """Build a CCW GeoJSON polygon string from [W, S, E, N]."""
    w, s, e, n = bbox
    return (
        '{"type":"Polygon","coordinates":[['
        f'[{w},{s}],[{e},{s}],[{e},{n}],[{w},{n}],[{w},{s}]'
        "]]}"
    )


def _rows():
    """Yield (slug, name, region, geojson_str, description, linked_gauges) for
    every area — bbox envelopes first, then real-geometry zones from the manifest."""
    for slug, name, region, bbox, desc, gauges in AREAS:
        yield slug, name, region, bbox_geojson(bbox), desc, gauges
    if ZONE_MANIFEST.exists():
        zones = json.loads(ZONE_MANIFEST.read_text(encoding="utf-8"))
        for zslug, z in zones.items():
            yield (zslug, z["name"], z["region"], json.dumps(z["geometry"]),
                   z["description"], z["linked_gauges"])
    else:
        print(f"WARNING: {ZONE_MANIFEST.name} not found — seeding bbox areas only. "
              "Run build_zone_geoms.py first to add the real zones.")


def main() -> None:
    rows = list(_rows())
    desired = {r[0] for r in rows}
    with conn() as c:
        for slug, name, region, geojson, desc, gauges in rows:
            c.execute(
                """
                INSERT INTO areas (name, slug, region, area_type, geom, description, linked_gauges)
                VALUES (
                  %s, %s, %s, 'predefined',
                  ST_MakeValid(ST_Force2D(ST_ForcePolygonCCW(
                    ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                  ))),
                  %s, %s
                )
                ON CONFLICT (slug) DO UPDATE SET
                  name          = EXCLUDED.name,
                  region        = EXCLUDED.region,
                  geom          = EXCLUDED.geom,
                  description   = EXCLUDED.description,
                  linked_gauges = EXCLUDED.linked_gauges
                """,
                (name, slug, region, geojson, desc, gauges),
            )

        # Prune any DB area no longer in the seed set, so removing an area is a
        # one-step edit (delete the tuple / manifest entry and re-run). DELETE
        # cascades to the area's indicators + snapshots. Guarded: skip when the
        # manifest is absent, so a missing zone_geoms.json can't wipe the zones.
        pruned: list[str] = []
        if ZONE_MANIFEST.exists():
            existing = [s for (s,) in c.execute("SELECT slug FROM areas").fetchall()]
            for slug in existing:
                if slug not in desired:
                    c.execute("DELETE FROM areas WHERE slug = %s", (slug,))
                    pruned.append(slug)
        else:
            print("WARNING: zone_geoms.json absent — skipping prune (won't touch zone areas).")

    n_zones = len(rows) - len(AREAS)
    summary = (f"Seeded {len(rows)} areas ({len(AREAS)} bbox + {n_zones} real-geometry zones); "
               f"pruned {len(pruned)}")
    print(summary + (f": {', '.join(pruned)}." if pruned else "."))


if __name__ == "__main__":
    main()
