"""Seed USGS river gauges from Section 3 of the plan.

Lat/lon are approximate (to ~0.01°). The hourly USGS fetch task corrects them
from the official NWIS site metadata on first sync — schema just needs values
for the NOT NULL constraint.
"""
from _db import conn

# (site_no, name, river, lat, lon, region)
GAUGES = [
    # --- Gulf tributaries ---
    # Tarbert Landing (07295100) is USACE-published and not in USGS NWIS;
    # use Baton Rouge instead for lower-Mississippi discharge.
    ("07374000", "Mississippi River at Baton Rouge, LA",       "Mississippi",  30.439, -91.193, "gulf"),
    ("07381490", "Atchafalaya River at Simmesport, LA",        "Atchafalaya",  30.985, -91.800, "gulf"),
    ("02469761", "Mobile River at Mount Vernon, AL",           "Mobile",       31.087, -88.001, "gulf"),
    ("02358000", "Apalachicola River at Chattahoochee, FL",    "Apalachicola", 30.701, -84.858, "gulf"),
    ("08028500", "Sabine River at Ruliff, TX",                 "Sabine",       30.302, -93.748, "gulf"),
    ("08066500", "Trinity River at Romayor, TX",               "Trinity",      30.425, -94.852, "gulf"),
    # --- East Coast ---
    ("01578310", "Susquehanna River at Conowingo, MD",         "Susquehanna",  39.658, -76.175, "east_coast"),
    ("02037500", "James River at Richmond, VA",                "James",        37.563, -77.547, "east_coast"),
    ("02089000", "Neuse River at Kinston, NC",                 "Neuse",        35.258, -77.586, "east_coast"),
    ("02080500", "Roanoke River at Roanoke Rapids, NC",        "Roanoke",      36.461, -77.638, "east_coast"),
]


def main() -> None:
    with conn() as c:
        for site_no, name, river, lat, lon, region in GAUGES:
            c.execute(
                """
                INSERT INTO usgs_gauges (site_no, name, river, lat, lon, region)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (site_no) DO UPDATE SET
                  name = EXCLUDED.name,
                  river = EXCLUDED.river,
                  region = EXCLUDED.region
                """,
                (site_no, name, river, lat, lon, region),
            )
    print(f"Seeded {len(GAUGES)} USGS gauges.")


if __name__ == "__main__":
    main()
