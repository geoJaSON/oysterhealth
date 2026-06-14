"""NOAA National Water Model streamflow FORECASTS via the NWPS API.

For each seeded gauge that has an `nwm_reach_id`, pull the short-range (~18 h)
and medium-range-blend (~10 day) forecast hydrographs and upsert them into
`nwm_forecasts`. Flow is ft^3/s (cfs) — the NWPS native unit, same as
`gauge_readings.discharge_cfs`, so no conversion is needed.

The freshwater_forecast indicator (Phase 4) reads the latest issuance's
trajectory for each zone's linked gauges and compares it to the recent baseline
to predict a freshwater pulse (or low-flow) days ahead — the forward-looking
companion to the present-tense freshwater_intrusion indicator.

Mirrors usgs.py: pure module functions; the Celery worker is a thin wrapper, and
the manage.py `fetch-nwm` command calls sync_forecasts() directly.

API shape (verified against the live service):
  GET /reaches/{reachId}/streamflow?series=<series>
  -> { "<seriesKey>": { "series": { "referenceTime", "units": "ft^3/s",
                                     "data": [ {"validTime", "flow"} ] } }, ... }
Only the requested series' key is populated; the others come back empty.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
import psycopg

from settings import settings

log = logging.getLogger(__name__)

NWPS_REACH_URL = "https://api.water.noaa.gov/nwps/v1/reaches/{reach}/streamflow"

# requested series name -> response key holding {series: {referenceTime, data}}.
# medium_range_blend is the single deterministic ~10-day trajectory (avoids the
# 6-member medium_range ensemble); short_range is the high-confidence ~18 h.
SERIES: dict[str, str] = {
    "short_range": "shortRange",
    "medium_range_blend": "mediumRangeBlend",
}


@dataclass
class ForecastPoint:
    site_no: str
    reach_id: str
    series: str
    issued_at: datetime
    valid_time: datetime
    flow_cfs: float | None


def _iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fetch_series(client: httpx.Client, reach: str, series: str) -> dict:
    resp = client.get(NWPS_REACH_URL.format(reach=reach), params={"series": series})
    resp.raise_for_status()
    return resp.json()


def _parse(payload: dict, site_no: str, reach_id: str, series: str, resp_key: str) -> list[ForecastPoint]:
    ser = ((payload or {}).get(resp_key) or {}).get("series") or {}
    ref = ser.get("referenceTime")
    data = ser.get("data") or []
    if not ref or not data:
        return []
    issued = _iso(ref)
    points: list[ForecastPoint] = []
    for pt in data:
        vt = pt.get("validTime")
        if not vt:
            continue
        flow = pt.get("flow")
        points.append(ForecastPoint(
            site_no, reach_id, series, issued, _iso(vt),
            float(flow) if flow is not None else None,
        ))
    return points


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _gauges_with_reach(conn: psycopg.Connection) -> list[tuple]:
    cur = conn.execute(
        "SELECT id, site_no, nwm_reach_id FROM usgs_gauges "
        "WHERE nwm_reach_id IS NOT NULL ORDER BY site_no"
    )
    return cur.fetchall()


def upsert_forecasts(conn: psycopg.Connection, points: list[ForecastPoint],
                     id_by_site: dict[str, str]) -> int:
    rows = [
        (id_by_site[p.site_no], p.reach_id, p.series, p.issued_at, p.valid_time, p.flow_cfs)
        for p in points if p.site_no in id_by_site
    ]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO nwm_forecasts (gauge_id, reach_id, series, issued_at, valid_time, flow_cfs)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (gauge_id, series, issued_at, valid_time) DO UPDATE
              SET flow_cfs = EXCLUDED.flow_cfs, reach_id = EXCLUDED.reach_id
            """,
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------

def sync_forecasts() -> dict[str, int]:
    """Fetch short-range + medium-range-blend forecasts for every gauge with a
    reach and upsert them. Returns a small report. Best-effort per gauge/series:
    NWPS occasionally 502/504s a single reach, which is logged and skipped rather
    than failing the whole run."""
    report = {"gauges": 0, "points": 0, "errors": 0}
    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        gauges = _gauges_with_reach(conn)
        id_by_site = {site_no: gid for gid, site_no, _reach in gauges}
        all_points: list[ForecastPoint] = []
        with httpx.Client(timeout=40.0, headers={"User-Agent": "OysterHealth/1.0"}) as client:
            for _gid, site_no, reach in gauges:
                got = False
                for series, resp_key in SERIES.items():
                    try:
                        payload = _fetch_series(client, reach, series)
                        pts = _parse(payload, site_no, reach, series, resp_key)
                        all_points.extend(pts)
                        got = got or bool(pts)
                    except Exception as e:  # noqa: BLE001
                        report["errors"] += 1
                        log.warning("NWM %s reach %s %s: %s", site_no, reach, series, e)
                if got:
                    report["gauges"] += 1
        report["points"] = upsert_forecasts(conn, all_points, id_by_site)
    log.info("NWM sync_forecasts: %s", report)
    return report
