"""Seed USGS river gauges from Section 3 of the plan.

Lat/lon are approximate (to ~0.01°). The hourly USGS fetch task corrects them
from the official NWIS site metadata on first sync — schema just needs values
for the NOT NULL constraint.

`nwm_reach_id` is the NOAA National Water Model reach (NHDPlus COMID) used for
streamflow FORECASTS via the NWPS API. It was resolved by NLDI coordinate-lookup
(POINT at the gauge) and magnitude-validated against each gauge's observed
discharge (all 0.5–1.4× — NWM tracks even the Mississippi/Atchafalaya well).
Two notes from that validation:
  - Neuse (02089000) coordinate-snaps to a tiny side channel ("Stoney Creek"),
    so it is OVERRIDDEN to the main-stem reach 11239465 (the KINN7 forecast
    point's reach), which matches the gauge 1.02×.
  - A few reaches carry an odd NHDPlus GNIS name (Trinity→"Fields Bayou",
    Tombigbee→"Flat Woods Creek", Tar→"Hendricks Creek") but carry main-stem-
    scale flow, so they are correct for discharge magnitude.
"""
from _db import conn

# (site_no, name, river, lat, lon, region, nwm_reach_id)
GAUGES = [
    # --- Gulf tributaries ---
    # Tarbert Landing (07295100) is USACE-published and not in USGS NWIS;
    # use Baton Rouge instead for lower-Mississippi discharge.
    ("07374000", "Mississippi River at Baton Rouge, LA",       "Mississippi",  30.439, -91.193, "gulf",       "19088319"),
    ("07381490", "Atchafalaya River at Simmesport, LA",        "Atchafalaya",  30.985, -91.800, "gulf",       "15169567"),
    # The Mobile-Tensaw delta is fed by two arms that join to form the Mobile R:
    # the Tombigbee (gauged at Coffeeville L&D) and the Alabama (Claiborne L&D).
    # NWIS 02469761 is the Tombigbee — NOT "Mobile R at Mt Vernon" (that's the
    # discontinued 02470500, no data since 1955). Both arms are linked to Mobile
    # Bay so the freshwater indicator sees ~93% of delta inflow, not ~43%.
    ("02469761", "Tombigbee River at Coffeeville L&D, AL",     "Tombigbee",    31.758, -88.129, "gulf",       "18548516"),
    ("02428400", "Alabama River at Claiborne L&D, AL",         "Alabama",      31.615, -87.551, "gulf",       "18239647"),
    ("02358000", "Apalachicola River at Chattahoochee, FL",    "Apalachicola", 30.701, -84.858, "gulf",       "2293124"),
    ("08028500", "Sabine River at Ruliff, TX",                 "Sabine",       30.302, -93.748, "gulf",       "8330800"),
    ("08066500", "Trinity River at Romayor, TX",               "Trinity",      30.425, -94.852, "gulf",       "1513293"),
    # San Jacinto system + Buffalo Bayou feed the upper-west / Houston Ship
    # Channel side of Galveston Bay. The Trinity is only ~50% of the estuary's
    # freshwater; without these the upper-bay inflow index misses ~40-50%.
    ("08068000", "West Fork San Jacinto River nr Conroe, TX",  "San Jacinto",  30.245, -95.457, "gulf",       "1468280"),
    ("08070000", "East Fork San Jacinto River nr Cleveland, TX","San Jacinto", 30.337, -95.104, "gulf",       "1520007"),
    ("08074000", "Buffalo Bayou at Houston, TX",               "Buffalo Bayou",29.760, -95.409, "gulf",       "1440301"),
    # --- East Coast ---
    ("01578310", "Susquehanna River at Conowingo, MD",         "Susquehanna",  39.658, -76.175, "east_coast", "4726595"),
    ("02037500", "James River at Richmond, VA",                "James",        37.563, -77.547, "east_coast", "8574021"),
    ("02089000", "Neuse River at Kinston, NC",                 "Neuse",        35.258, -77.586, "east_coast", "11239465"),
    ("02080500", "Roanoke River at Roanoke Rapids, NC",        "Roanoke",      36.461, -77.638, "east_coast", "10451190"),
    # Potomac is the 2nd-largest Chesapeake tributary (enters the mid bay ~38.0N)
    # and had no gauge; Little Falls is the standard non-tidal discharge index.
    ("01646500", "Potomac River near Washington, DC (Little Falls)", "Potomac", 38.950, -77.128, "east_coast", "4512772"),
    # Tar-Pamlico feeds the NW lobe of Pamlico Sound (~47% of inflow), previously
    # ungauged; Tarboro is the lowest non-tidal Tar River gauge.
    ("02083500", "Tar River at Tarboro, NC",                   "Tar-Pamlico",  35.894, -77.533, "east_coast", "3350831"),
]


def main() -> None:
    with conn() as c:
        for site_no, name, river, lat, lon, region, reach in GAUGES:
            c.execute(
                """
                INSERT INTO usgs_gauges (site_no, name, river, lat, lon, region, nwm_reach_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (site_no) DO UPDATE SET
                  name         = EXCLUDED.name,
                  river        = EXCLUDED.river,
                  region       = EXCLUDED.region,
                  nwm_reach_id = EXCLUDED.nwm_reach_id
                """,
                (site_no, name, river, lat, lon, region, reach),
            )
    print(f"Seeded {len(GAUGES)} USGS gauges (with NWM reach IDs).")


if __name__ == "__main__":
    main()
