"""Seed NOAA CO-OPS stations near each predefined area.

Sourced from the public CO-OPS station list. The hourly station-readings task
backfills the actual lat/lon and metadata on first sync; values here are close
enough for the "nearest station within 15 km" check used by the oyster drill
indicator.
"""
from _db import conn

# (station_id, name, lat, lon, variables)
STATIONS = [
    # --- Gulf ---
    ("8761724", "Grand Isle, LA",                    29.263, -89.957, ["water_temperature", "salinity", "water_level"]),
    ("8762075", "Port Fourchon, Belle Pass, LA",     29.114, -90.199, ["water_temperature", "water_level"]),
    ("8761955", "Carrollton, New Orleans, LA",       29.934, -90.131, ["water_level"]),
    ("8747437", "Bay Waveland Yacht Club, MS",       30.326, -89.325, ["water_temperature", "water_level"]),
    ("8735180", "Dauphin Island, AL",                30.250, -88.075, ["water_temperature", "salinity", "water_level"]),
    ("8729108", "Panama City, FL",                   30.152, -85.667, ["water_temperature", "water_level"]),
    ("8728690", "Apalachicola, FL",                  29.727, -84.981, ["water_temperature", "water_level"]),
    ("8726520", "St. Petersburg, FL",                27.761, -82.626, ["water_temperature", "water_level"]),
    ("8725110", "Naples, FL",                        26.131, -81.807, ["water_temperature", "water_level"]),
    ("8771341", "Galveston Bay Entrance, TX",        29.357, -94.725, ["water_temperature", "salinity", "water_level"]),
    ("8773701", "Port O'Connor, TX",                 28.452, -96.395, ["water_temperature", "water_level"]),
    ("8775870", "Bob Hall Pier, Corpus Christi, TX", 27.580, -97.217, ["water_temperature", "water_level"]),
    ("8770475", "Port Arthur, TX",                   29.867, -93.930, ["water_temperature", "water_level"]),

    # --- East Coast ---
    ("8574680", "Baltimore, MD",                     39.267, -76.580, ["water_temperature", "water_level"]),
    ("8638610", "Sewells Point, VA",                 36.947, -76.330, ["water_temperature", "salinity", "water_level"]),
    ("8557380", "Lewes, DE",                         38.782, -75.119, ["water_temperature", "water_level"]),
    ("8651370", "Duck, NC",                          36.183, -75.747, ["water_temperature", "water_level"]),
    ("8656483", "Beaufort, NC",                      34.720, -76.670, ["water_temperature", "water_level"]),
    ("8665530", "Charleston, SC",                    32.781, -79.925, ["water_temperature", "water_level"]),
    ("8720218", "Mayport, FL",                       30.398, -81.428, ["water_temperature", "water_level"]),
    ("8722670", "Lake Worth Pier, FL",               26.613, -80.034, ["water_temperature", "water_level"]),
    ("8461490", "New London, CT",                    41.355, -72.087, ["water_temperature", "water_level"]),
    ("8467150", "Bridgeport, CT",                    41.173, -73.182, ["water_temperature", "water_level"]),
]


def main() -> None:
    with conn() as c:
        for station_id, name, lat, lon, variables in STATIONS:
            c.execute(
                """
                INSERT INTO stations (station_id, name, lat, lon, variables)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (station_id) DO UPDATE SET
                  name = EXCLUDED.name,
                  lat = EXCLUDED.lat,
                  lon = EXCLUDED.lon,
                  variables = EXCLUDED.variables
                """,
                (station_id, name, lat, lon, variables),
            )
    print(f"Seeded {len(STATIONS)} NOAA CO-OPS stations.")


if __name__ == "__main__":
    main()
