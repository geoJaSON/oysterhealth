"""USGS NWIS Water Services client + upsert into gauge_readings.

The Celery worker in api/workers/usgs.py is a thin wrapper around the
functions here. Importing them directly works fine for manual testing and
for the backfill script — no Celery worker required.

NWIS endpoints used:
  - iv  (instantaneous values, ~15-min cadence): https://waterservices.usgs.gov/nwis/iv/
  - dv  (daily values, one row per day):         https://waterservices.usgs.gov/nwis/dv/

Parameter codes:
  - 00060 — discharge (cubic feet / second)
  - 00065 — gage height (feet)
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import httpx
import psycopg

from settings import settings

log = logging.getLogger(__name__)

NWIS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
NWIS_DV_URL = "https://waterservices.usgs.gov/nwis/dv/"

PARAM_DISCHARGE = "00060"
PARAM_STAGE = "00065"

# NWIS uses this sentinel for missing readings
MISSING_SENTINEL = "-999999"


@dataclass
class Reading:
    site_no: str
    recorded_at: datetime
    discharge_cfs: float | None = None
    stage_ft: float | None = None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _fetch_nwis(url: str, sites: list[str], **params) -> dict:
    """Single NWIS request. Caller chooses iv vs. dv via the URL.

    Raises httpx.HTTPError on non-2xx (Celery's retry_backoff_task catches).
    """
    if not sites:
        return {"value": {"timeSeries": []}}
    query = {
        "format": "json",
        "sites": ",".join(sites),
        "parameterCd": f"{PARAM_DISCHARGE},{PARAM_STAGE}",
        **params,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=query)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_value(s: str) -> float | None:
    if s is None or s == MISSING_SENTINEL:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    # NWIS sometimes returns -999999.0 etc.
    return None if v <= -999998 else v


@dataclass
class SiteInfo:
    site_no: str
    name: str
    lat: float | None
    lon: float | None


def parse_timeseries(payload: dict) -> tuple[list[Reading], dict[str, SiteInfo]]:
    """Merge USGS's per-variable timeseries into per-(site, time) Reading rows.

    Also collects the authoritative siteName and geoLocation USGS returns so
    callers can update the approximate values seeded in usgs_gauges.
    """
    by_key: dict[tuple[str, datetime], Reading] = {}
    sites: dict[str, SiteInfo] = {}

    for ts in payload.get("value", {}).get("timeSeries", []):
        src = ts["sourceInfo"]
        site_no = src["siteCode"][0]["value"]
        if site_no not in sites:
            geo = src.get("geoLocation", {}).get("geogLocation", {})
            sites[site_no] = SiteInfo(
                site_no=site_no,
                name=src.get("siteName", ""),
                lat=geo.get("latitude"),
                lon=geo.get("longitude"),
            )

        param = ts["variable"]["variableCode"][0]["value"]
        for v in ts["values"][0]["value"]:
            value = _parse_value(v.get("value"))
            if value is None:
                continue
            recorded_at = datetime.fromisoformat(v["dateTime"])
            key = (site_no, recorded_at)
            r = by_key.setdefault(key, Reading(site_no, recorded_at))
            if param == PARAM_DISCHARGE:
                r.discharge_cfs = value
            elif param == PARAM_STAGE:
                r.stage_ft = value

    return list(by_key.values()), sites


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def upsert_site_metadata(conn: psycopg.Connection, sites: dict[str, SiteInfo]) -> None:
    """Refresh siteName + lat/lon on usgs_gauges from authoritative NWIS data.

    Doesn't touch `river` or `region` — those are operator-managed.
    """
    rows = [
        (s.name, s.lat, s.lon, s.site_no)
        for s in sites.values()
        if s.name and s.lat is not None and s.lon is not None
    ]
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE usgs_gauges
               SET name = %s, lat = %s, lon = %s
             WHERE site_no = %s
            """,
            rows,
        )


def upsert_readings(conn: psycopg.Connection, readings: Iterable[Reading]) -> int:
    """Map site_no → gauge_id once, then bulk upsert."""
    cur = conn.execute("SELECT id, site_no FROM usgs_gauges")
    id_by_site = {row[1]: row[0] for row in cur.fetchall()}

    rows: list[tuple] = []
    for r in readings:
        gauge_id = id_by_site.get(r.site_no)
        if gauge_id is None:
            continue  # site returned by NWIS that we don't have seeded — skip
        rows.append((gauge_id, r.recorded_at, r.discharge_cfs, r.stage_ft))

    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO gauge_readings (gauge_id, recorded_at, discharge_cfs, stage_ft)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (gauge_id, recorded_at) DO UPDATE
              SET discharge_cfs = EXCLUDED.discharge_cfs,
                  stage_ft      = EXCLUDED.stage_ft
            """,
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Public orchestrators
# ---------------------------------------------------------------------------

def _all_site_nos(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute("SELECT site_no FROM usgs_gauges ORDER BY site_no")
    return [r[0] for r in cur.fetchall()]


def sync_latest(period: str = "PT2H") -> int:
    """Pull the most-recent instantaneous values for every seeded gauge.

    `period` follows ISO 8601 — PT2H = last 2 hours. Hourly scheduling uses
    PT2H so we always overlap the previous run a bit.
    """
    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        sites = _all_site_nos(conn)
        log.info("USGS sync_latest: %d sites, period=%s", len(sites), period)
        payload = _fetch_nwis(NWIS_IV_URL, sites, period=period)
        readings, site_info = parse_timeseries(payload)
        upsert_site_metadata(conn, site_info)
        n = upsert_readings(conn, readings)
        log.info("USGS sync_latest: upserted %d readings", n)
        return n


def sync_historical_daily(days_back: int = 90) -> int:
    """One-shot backfill of daily values. Cheap on NWIS and gives the
    indicator math a stable 30-day mean to work against.
    """
    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        sites = _all_site_nos(conn)
        log.info("USGS sync_historical_daily: %d sites, days_back=%d",
                 len(sites), days_back)
        payload = _fetch_nwis(NWIS_DV_URL, sites, period=f"P{days_back}D")
        readings, site_info = parse_timeseries(payload)
        upsert_site_metadata(conn, site_info)
        n = upsert_readings(conn, readings)
        log.info("USGS sync_historical_daily: upserted %d readings", n)
        return n
