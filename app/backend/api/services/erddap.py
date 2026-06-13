"""NOAA CoastWatch ERDDAP client + per-area aggregator → data_snapshots.

ERDDAP returns gridded NetCDF data; we ask for the bbox slice in JSON and
reduce it to (mean, min, max) per area per variable. Per Phase 1, we
aggregate over the polygon's *bounding box* rather than the polygon itself —
once areas have real coastline polygons we'll switch to point-in-polygon
masking (shapely + grid cell centers).

Dataset IDs change occasionally on ERDDAP; if a fetch fails with 404 or an
empty result, the first place to look is `DATASETS` below and the live
catalog at https://coastwatch.noaa.gov/erddap/info/index.html.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import psycopg

from settings import settings

log = logging.getLogger(__name__)

COASTWATCH = "https://coastwatch.noaa.gov/erddap"


@dataclass(frozen=True)
class ErddapDataset:
    """How to fetch one variable from ERDDAP.

    `variable` is the internal name we store in `data_snapshots.variable`
    (must match the CHECK constraint there). `data_var` is the column name
    inside the ERDDAP dataset's JSON response.
    """
    variable: str
    server: str
    dataset_id: str
    data_var: str
    units: str
    has_altitude: bool = False


# Edit these to swap datasets without touching code.
DATASETS: dict[str, ErddapDataset] = {
    "sst": ErddapDataset(
        variable="sst",
        server=COASTWATCH,
        dataset_id="noaacwBLENDEDsstDNDaily",
        data_var="analysed_sst",
        units="degree_C",
    ),
    "chlorophyll": ErddapDataset(
        variable="chlorophyll",
        server=COASTWATCH,
        # DINEOF gap-filled VIIRS+OLCI; daily, 2km, fewer "no data" holes
        # near cloudy coasts than raw VIIRS.
        dataset_id="noaacwNPPN20S3ASCIDINEOF2kmDaily",
        data_var="chlor_a",
        units="mg m-3",
        has_altitude=True,
    ),
    "turbidity": ErddapDataset(
        variable="turbidity",
        server=COASTWATCH,
        # Kd_490 (diffuse attenuation at 490nm) is the standard satellite
        # proxy for turbidity — higher Kd = more attenuation = murkier water.
        # "SectorUS" is the merged CONUS coastal dataset.
        dataset_id="noaacwN20VIIRSkd490SectorUSDaily",
        data_var="kd_490",
        units="m-1",
        has_altitude=True,
    ),
}


@dataclass
class GridAggregate:
    captured_at: datetime
    value_mean: float
    value_min: float
    value_max: float
    cell_count: int


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _grid_url(ds: ErddapDataset, bbox: tuple[float, float, float, float], time_idx: str) -> str:
    """Build the unusual ERDDAP griddap URL: variable + bracketed indices.

    bbox = (west, south, east, north). ERDDAP wants lat range before lon range.
    """
    w, s, e, n = bbox
    parts = [f"[{time_idx}]"]
    if ds.has_altitude:
        parts.append("[(0.0)]")
    parts.append(f"[({s}):({n})]")
    parts.append(f"[({w}):({e})]")
    query = ds.data_var + "".join(parts)
    return f"{ds.server}/griddap/{ds.dataset_id}.json?{query}"


def fetch_grid_json(ds: ErddapDataset, bbox, time_idx: str = "(last)") -> dict:
    """Single ERDDAP griddap request. Raises httpx.HTTPStatusError on non-2xx."""
    url = _grid_url(ds, bbox, time_idx)
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def fetch_latest_time(ds: ErddapDataset) -> datetime:
    """Return the dataset's most recent time index.

    Used to build backfill date ranges that don't overrun the dataset's lag
    (chlorophyll is often 10+ days behind real time even when SST is fresh).
    """
    url = f"{ds.server}/griddap/{ds.dataset_id}.json?time[(last)]"
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        rows = resp.json()["table"]["rows"]
        return datetime.fromisoformat(rows[0][0].replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Parsing / aggregation
# ---------------------------------------------------------------------------

def aggregate_payload(payload: dict, data_var: str) -> list[GridAggregate]:
    """Reduce an ERDDAP grid response to one GridAggregate per distinct time.

    Single-timestamp responses return a list of length 1; range queries
    return one entry per day. None values (clouds, land masking) are skipped.
    """
    table = payload.get("table", {})
    cols = table.get("columnNames", [])
    rows = table.get("rows", [])
    if not rows:
        return []

    try:
        time_idx = cols.index("time")
        value_idx = cols.index(data_var)
    except ValueError:
        log.warning("ERDDAP response missing expected columns: %s", cols)
        return []

    by_time: dict[str, list[float]] = {}
    for r in rows:
        v = r[value_idx]
        if v is None:
            continue
        by_time.setdefault(r[time_idx], []).append(v)

    out: list[GridAggregate] = []
    for t, values in by_time.items():
        out.append(
            GridAggregate(
                captured_at=datetime.fromisoformat(t.replace("Z", "+00:00")),
                value_mean=sum(values) / len(values),
                value_min=min(values),
                value_max=max(values),
                cell_count=len(values),
            )
        )
    return sorted(out, key=lambda a: a.captured_at)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _all_areas(conn: psycopg.Connection):
    cur = conn.execute(
        """
        SELECT id, slug,
               ST_XMin(geom) AS w, ST_YMin(geom) AS s,
               ST_XMax(geom) AS e, ST_YMax(geom) AS n
          FROM areas
         ORDER BY slug
        """
    )
    return cur.fetchall()


def upsert_snapshot(
    conn: psycopg.Connection,
    area_id: str,
    ds: ErddapDataset,
    agg: GridAggregate,
) -> None:
    conn.execute(
        """
        INSERT INTO data_snapshots
              (area_id, captured_at, variable, value_mean, value_min, value_max, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (area_id, variable, captured_at) DO UPDATE
          SET value_mean = EXCLUDED.value_mean,
              value_min  = EXCLUDED.value_min,
              value_max  = EXCLUDED.value_max,
              source     = EXCLUDED.source
        """,
        (
            area_id,
            agg.captured_at,
            ds.variable,
            agg.value_mean,
            agg.value_min,
            agg.value_max,
            f"erddap:{ds.dataset_id}",
        ),
    )


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

def _sync_with_time_idx(variable_key: str, time_idx: str) -> dict:
    """Fetch + upsert for one ERDDAP time index across every area.

    `time_idx` is whatever goes between the first pair of brackets in the
    griddap URL — either `(last)` for the most recent, or a range like
    `(2026-04-22):(2026-05-22)` for a backfill window.
    """
    ds = DATASETS.get(variable_key)
    if ds is None:
        raise ValueError(f"Unknown ERDDAP variable: {variable_key!r}")

    report = {"ok": 0, "empty": 0, "error": 0, "rows": 0, "areas": {}}

    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        for area_id, slug, w, s, e, n in _all_areas(conn):
            bbox = (float(w), float(s), float(e), float(n))
            try:
                payload = fetch_grid_json(ds, bbox, time_idx=time_idx)
                aggs = aggregate_payload(payload, ds.data_var)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 416):
                    aggs = []
                else:
                    log.exception("ERDDAP %s for area %s", variable_key, slug)
                    report["error"] += 1
                    report["areas"][slug] = f"error: HTTP {exc.response.status_code}"
                    continue
            except Exception as exc:  # noqa: BLE001
                log.exception("ERDDAP %s for area %s", variable_key, slug)
                report["error"] += 1
                report["areas"][slug] = f"error: {type(exc).__name__}"
                continue

            if not aggs:
                report["empty"] += 1
                report["areas"][slug] = "no data"
                continue

            for agg in aggs:
                upsert_snapshot(conn, area_id, ds, agg)
            report["ok"] += 1
            report["rows"] += len(aggs)
            report["areas"][slug] = (
                f"{len(aggs)} timestep(s); latest "
                f"{aggs[-1].value_mean:.3f} {ds.units} "
                f"@ {aggs[-1].captured_at.isoformat()}"
            )

    log.info(
        "ERDDAP %s [%s]: %d ok areas, %d empty, %d error, %d total rows",
        variable_key, time_idx,
        report["ok"], report["empty"], report["error"], report["rows"],
    )
    return report


def sync_variable(variable_key: str) -> dict:
    """Fetch the latest grid for `variable_key` across every area."""
    return _sync_with_time_idx(variable_key, "(last)")


def sync_variable_range(variable_key: str, days_back: int) -> dict:
    """Backfill the last `days_back` days for `variable_key`.

    Queries the dataset's actual latest time first so we don't overrun the
    lag (chlorophyll is often 10+ days behind SST). Note: ERDDAP's
    `(last-N)` syntax uses the dimension's native units, which for time is
    *seconds* — not days — so the obvious shorthand doesn't work here.
    """
    if days_back < 1:
        raise ValueError("days_back must be >= 1")
    ds = DATASETS.get(variable_key)
    if ds is None:
        raise ValueError(f"Unknown ERDDAP variable: {variable_key!r}")

    latest = fetch_latest_time(ds).date()
    start = latest - timedelta(days=days_back)
    time_idx = f"({start.isoformat()}):({latest.isoformat()})"
    return _sync_with_time_idx(variable_key, time_idx)


def sync_all() -> dict[str, dict]:
    """Run all configured datasets in sequence. Used by the daily worker."""
    return {key: sync_variable(key) for key in DATASETS}
