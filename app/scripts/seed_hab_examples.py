"""Seed example HAB alerts.

These polygons represent recurring real-world bloom regions so the UI has
something meaningful to render until a live fetcher is wired in. Replace by
deleting these rows and running the fetcher once a stable source is wired:

  DELETE FROM hab_alerts WHERE description LIKE 'EXAMPLE:%';

Sources used to draw the regions:
  - FL Karenia brevis: typical SW Florida summer/fall bloom footprint
  - Mississippi Sound cyanobacteria: post-Bonnet Carré spillway pattern
  - Pamlico Sound dinoflagellate: spring bloom along Neuse River mouth
"""
from datetime import datetime, timedelta, timezone

from _db import conn

NOW = datetime.now(timezone.utc)
SOON = NOW + timedelta(days=14)

# (region label, alert_level, species, description, geojson polygon)
ALERTS = [
    (
        "SW Florida Gulf Coast",
        "warning",
        "Karenia brevis",
        "EXAMPLE: Moderate-to-high K. brevis cell counts reported along the SW Florida coast. "
        "Respiratory irritation possible at beaches; commercial harvest closures likely if bloom intensifies.",
        {
            "type": "Polygon",
            "coordinates": [[
                [-82.85, 26.45], [-81.80, 26.45],
                [-81.80, 28.10], [-82.85, 28.10],
                [-82.85, 26.45],
            ]],
        },
    ),
    (
        "Mississippi Sound",
        "watch",
        "Microcystis (cyanobacteria)",
        "EXAMPLE: Elevated cyanobacteria detected near the western Mississippi Sound following "
        "freshwater discharge. Oyster harvest advisories pending water-quality verification.",
        {
            "type": "Polygon",
            "coordinates": [[
                [-89.35, 30.18], [-88.85, 30.18],
                [-88.85, 30.45], [-89.35, 30.45],
                [-89.35, 30.18],
            ]],
        },
    ),
    (
        "Pamlico Sound (Neuse mouth)",
        "watch",
        "Heterosigma akashiwo",
        "EXAMPLE: Dinoflagellate bloom reported at the mouth of the Neuse River. Monitor for "
        "fish kills and finfish stress; oyster impact typically minimal.",
        {
            "type": "Polygon",
            "coordinates": [[
                [-76.65, 35.00], [-76.30, 35.00],
                [-76.30, 35.20], [-76.65, 35.20],
                [-76.65, 35.00],
            ]],
        },
    ),
]


def main() -> None:
    import json
    with conn() as c:
        # Clear prior EXAMPLE rows so the script is idempotent
        c.execute("DELETE FROM hab_alerts WHERE description LIKE 'EXAMPLE:%'")
        for region, level, species, desc, geom in ALERTS:
            c.execute(
                """
                INSERT INTO hab_alerts
                    (region, alert_level, species, description, issued_at, expires_at, geom)
                VALUES (%s, %s, %s, %s, %s, %s,
                        ST_MakeValid(ST_Force2D(ST_ForcePolygonCCW(
                          ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                        ))))
                """,
                (region, level, species, desc, NOW, SOON, json.dumps(geom)),
            )
    print(f"Seeded {len(ALERTS)} example HAB alerts (DELETE WHERE description LIKE 'EXAMPLE:%' to clear).")


if __name__ == "__main__":
    main()
