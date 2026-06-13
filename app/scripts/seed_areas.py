"""Seed predefined coastal areas as PostGIS polygons.

Bounding boxes here are approximate first-cut envelopes for each named area.
Replace with NOAA/state-published coastline shapefiles when available — the
ID/slug stays stable so the geom column can be UPDATEd in place later.

All geometry passes through ST_MakeValid(ST_Force2D(ST_ForcePolygonCCW(...)))
so PostGIS receives a right-hand-rule, 2D, validated polygon every time.
"""
from _db import conn

# (slug, name, region, [west, south, east, north], description, linked_gauges)
AREAS = [
    # --- Gulf of Mexico ---
    ("barataria-bay", "Barataria Bay", "gulf",
     [-90.30, 29.30, -89.75, 29.65],
     "Louisiana estuary west of the Mississippi delta. Major oyster and shrimp ground.",
     ["07374000", "07381490"]),
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
    ("mobile-bay", "Mobile Bay", "gulf",
     [-88.10, 30.20, -87.80, 30.75],
     "Alabama's primary estuary. Mobile River discharge dominates salinity.",
     ["02469761"]),
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
    ("galveston-bay", "Galveston Bay", "gulf",
     [-95.10, 29.30, -94.65, 29.85],
     "Texas's primary oyster bay. Trinity River dominates salinity.",
     ["08066500"]),
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

    # --- East Coast ---
    ("chesapeake-bay", "Chesapeake Bay", "east_coast",
     [-76.80, 36.90, -75.80, 39.55],
     "Full bay, MD/VA. Sub-regions can be added as separate areas later.",
     ["01578310", "02037500"]),
    ("delaware-bay", "Delaware Bay", "east_coast",
     [-75.50, 38.80, -74.85, 39.45],
     "DE/NJ estuary fed by the Delaware River.",
     []),
    ("pamlico-sound", "Pamlico Sound", "east_coast",
     [-76.80, 34.95, -75.45, 35.85],
     "NC's main sound; fed by the Neuse and Tar–Pamlico rivers.",
     ["02089000"]),
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


def main() -> None:
    with conn() as c:
        for slug, name, region, bbox, desc, gauges in AREAS:
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
                (name, slug, region, bbox_geojson(bbox), desc, gauges),
            )
    print(f"Seeded {len(AREAS)} predefined areas.")


if __name__ == "__main__":
    main()
