"""NOAA CO-OPS (Tides & Currents) datagetter client + upsert into station_readings.

CO-OPS API quirks worth flagging:
  - Only one station per request. We loop through seeded stations.
  - Some products don't honor `interval=h`; those return 6-min raw data and we
    sample down to ~hourly client-side.
  - Errors come back in the JSON body (HTTP 200) as `{"error": {"message": ...}}`
    rather than via HTTP status. We check `error` explicitly.
  - Stations advertise which products they support via the `variables` column
    on the `stations` table (set in seed_stations.py). We only request what's
    declared, so a freshwater-light station doesn't get spammed for salinity.

API docs: https://api.tidesandcurrents.noaa.gov/api/prod/
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import psycopg

from settings import settings

log = logging.getLogger(__name__)

CO_OPS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

# Map our internal variable name → (CO-OPS product, units we want, db unit label)
PRODUCTS: dict[str, tuple[str, str, str]] = {
    "water_temperature": ("water_temperature", "metric", "degree_C"),
    "salinity":          ("salinity",          "metric", "psu"),
    "water_level":       ("water_level",       "metric", "m"),
}


@dataclass
class Reading:
    station_id: str          # NOAA station id (string), not our UUID
    variable: str
    recorded_at: datetime
    value: float
    unit: str


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _fetch(station_id: str, variable: str, **params) -> dict:
    """Single CO-OPS datagetter request. Returns the parsed JSON body.

    Returns an empty payload on 400/404 — CO-OPS uses 400 to mean "this
    station doesn't currently expose this product", which is normal (e.g.
    decommissioned salinity sensors). Real network failures still raise.
    """
    product, units, _unit_label = PRODUCTS[variable]
    query = {
        "station": station_id,
        "product": product,
        "units": units,
        "time_zone": "gmt",
        "format": "json",
        "datum": "MLLW",         # only used by water_level, ignored by others
        "application": "oysterhealth",
        **params,
    }
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(CO_OPS_URL, params=query)
        if resp.status_code in (400, 404):
            return {}
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_payload(payload: dict, station_id: str, variable: str) -> list[Reading]:
    """Convert a CO-OPS payload to Reading rows. Returns [] on error or empty."""
    if "error" in payload:
        return []
    rows = payload.get("data", [])
    if not rows:
        return []

    _product, _units, unit_label = PRODUCTS[variable]
    out: list[Reading] = []
    for row in rows:
        raw = row.get("v")
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        # CO-OPS timestamps are "YYYY-MM-DD HH:MM" in GMT (per time_zone=gmt above)
        recorded_at = datetime.strptime(row["t"], "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )
        out.append(Reading(station_id, variable, recorded_at, value, unit_label))
    return out


def _sample_hourly(readings: list[Reading]) -> list[Reading]:
    """Reduce sub-hourly readings (6-min CO-OPS cadence) to ~one per hour.

    Picks the first reading in each (UTC-hour, variable, station) bucket.
    Keeps `recorded_at` accurate (we don't fake it to top-of-hour).
    """
    seen: set[tuple[str, str, datetime]] = set()
    out: list[Reading] = []
    for r in readings:
        bucket = (r.station_id, r.variable, r.recorded_at.replace(minute=0, second=0, microsecond=0))
        if bucket in seen:
            continue
        seen.add(bucket)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _load_stations(conn: psycopg.Connection):
    """All seeded CO-OPS stations with their advertised variables."""
    cur = conn.execute(
        "SELECT id, station_id, variables FROM stations ORDER BY station_id"
    )
    return cur.fetchall()


def upsert_readings(conn: psycopg.Connection, readings: Iterable[Reading]) -> int:
    """Map CO-OPS station_id → our UUID once, then bulk upsert."""
    cur = conn.execute("SELECT id, station_id FROM stations")
    id_by_station = {r[1]: r[0] for r in cur.fetchall()}

    rows: list[tuple] = []
    for r in readings:
        uuid = id_by_station.get(r.station_id)
        if uuid is None:
            continue
        rows.append((uuid, r.recorded_at, r.variable, r.value, r.unit))

    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO station_readings (station_id, recorded_at, variable, value, unit)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (station_id, variable, recorded_at) DO UPDATE
              SET value = EXCLUDED.value, unit = EXCLUDED.unit
            """,
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

def sync_latest() -> dict:
    """Fetch the latest reading for every supported (station, variable) pair.

    Uses `date=latest` which returns one row per station — the most recent
    available 6-min sample.
    """
    report = {"requests": 0, "rows": 0, "stations_with_data": 0, "errors": 0}

    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        stations = _load_stations(conn)
        all_readings: list[Reading] = []

        for _uuid, station_id, variables in stations:
            station_had_data = False
            for variable in (variables or []):
                if variable not in PRODUCTS:
                    continue
                try:
                    payload = _fetch(station_id, variable, date="latest")
                    report["requests"] += 1
                except httpx.HTTPError:
                    log.exception("CO-OPS latest %s/%s", station_id, variable)
                    report["errors"] += 1
                    continue
                readings = _parse_payload(payload, station_id, variable)
                if readings:
                    station_had_data = True
                    all_readings.extend(readings)
            if station_had_data:
                report["stations_with_data"] += 1

        report["rows"] = upsert_readings(conn, all_readings)

    log.info(
        "CO-OPS sync_latest: %d requests, %d stations with data, %d rows, %d errors",
        report["requests"], report["stations_with_data"], report["rows"], report["errors"],
    )
    return report


def sync_historical(days_back: int = 30) -> dict:
    """Backfill the last `days_back` days for every supported (station, variable).

    CO-OPS limits each request to 31 days at 6-min cadence — fine for our 30-day
    default. We sample the response down to hourly before insert.
    """
    if days_back < 1 or days_back > 31:
        raise ValueError("days_back must be 1..31 (CO-OPS per-request limit)")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    begin_str = start.strftime("%Y%m%d %H:%M")
    end_str = end.strftime("%Y%m%d %H:%M")

    report = {"requests": 0, "rows": 0, "errors": 0}

    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        stations = _load_stations(conn)
        all_readings: list[Reading] = []

        for _uuid, station_id, variables in stations:
            for variable in (variables or []):
                if variable not in PRODUCTS:
                    continue
                try:
                    payload = _fetch(
                        station_id, variable,
                        begin_date=begin_str, end_date=end_str,
                    )
                    report["requests"] += 1
                except httpx.HTTPError:
                    log.exception("CO-OPS history %s/%s", station_id, variable)
                    report["errors"] += 1
                    continue
                readings = _parse_payload(payload, station_id, variable)
                if readings:
                    all_readings.extend(_sample_hourly(readings))

        report["rows"] = upsert_readings(conn, all_readings)

    log.info(
        "CO-OPS sync_historical (%dd): %d requests, %d rows, %d errors",
        days_back, report["requests"], report["rows"], report["errors"],
    )
    return report
