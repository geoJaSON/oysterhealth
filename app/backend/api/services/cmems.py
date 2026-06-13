"""Copernicus Marine (CMEMS) modeled surface salinity -> data_snapshots.

Fills the salinity gap where no NOAA CO-OPS station is near an area, so the
oyster-drill model has a salinity signal *coast-wide*. Uses the Global Ocean
Physics Analysis & Forecast surface salinity (`so`), daily mean, 1/12 deg.

Caveat surfaced in the UI as `modeled` confidence: at ~8 km the model
under-resolves estuary heads and tends to read oceanic salinity inside bays, so
treat it as a regional estimate, not in-situ truth. In-situ CO-OPS salinity,
when a station is near, always takes precedence in the synthesis layer.

Auth: set CMEMS_USERNAME / CMEMS_PASSWORD in .env (free account at
marine.copernicus.eu). They're exported as COPERNICUSMARINE_SERVICE_* env vars,
which the copernicusmarine client also reads.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import psycopg

from settings import settings

log = logging.getLogger(__name__)

# Global Ocean Physics Analysis & Forecast — surface salinity, daily mean, 1/12 deg.
DATASET_ID = "cmems_mod_glo_phy-so_anfc_0.083deg_P1D-m"
VARIABLE = "so"             # sea_water_salinity (practical salinity ~ psu)
SURFACE_MAX_DEPTH = 1.0     # shallowest model level (~0.49 m)

# One bounding region covering every seeded area (Gulf + East Coast). We open
# the dataset once for this box, then take per-area bbox slices in memory.
REGION = {"min_lon": -98.0, "max_lon": -70.0, "min_lat": 23.0, "max_lat": 42.0}


def _ensure_auth() -> None:
    if not (settings.CMEMS_USERNAME and settings.CMEMS_PASSWORD):
        raise RuntimeError(
            "CMEMS_USERNAME / CMEMS_PASSWORD must be set in .env "
            "(free account at marine.copernicus.eu)"
        )
    os.environ.setdefault("COPERNICUSMARINE_SERVICE_USERNAME", settings.CMEMS_USERNAME)
    os.environ.setdefault("COPERNICUSMARINE_SERVICE_PASSWORD", settings.CMEMS_PASSWORD)


def _np_to_dt(t) -> datetime:
    """numpy.datetime64 -> tz-aware UTC datetime."""
    import numpy as np
    ts = (t - np.datetime64("1970-01-01T00:00:00")) / np.timedelta64(1, "s")
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def _open_surface_salinity(days_back: int):
    """Lazily open the surface-salinity slice for the whole region + window."""
    import copernicusmarine

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back + 1)
    return copernicusmarine.open_dataset(
        dataset_id=DATASET_ID,
        username=settings.CMEMS_USERNAME,
        password=settings.CMEMS_PASSWORD,
        variables=[VARIABLE],
        minimum_longitude=REGION["min_lon"], maximum_longitude=REGION["max_lon"],
        minimum_latitude=REGION["min_lat"], maximum_latitude=REGION["max_lat"],
        minimum_depth=0.0, maximum_depth=SURFACE_MAX_DEPTH,
        start_datetime=start, end_datetime=end,
    )


def _areas(conn: psycopg.Connection):
    return conn.execute(
        """
        SELECT id, slug,
               ST_XMin(geom) AS w, ST_YMin(geom) AS s,
               ST_XMax(geom) AS e, ST_YMax(geom) AS n
          FROM areas
         ORDER BY slug
        """
    ).fetchall()


def _upsert(conn, area_id, captured_at, mean, vmin, vmax) -> None:
    conn.execute(
        """
        INSERT INTO data_snapshots
              (area_id, captured_at, variable, value_mean, value_min, value_max, source)
        VALUES (%s, %s, 'salinity', %s, %s, %s, %s)
        ON CONFLICT (area_id, variable, captured_at) DO UPDATE
          SET value_mean = EXCLUDED.value_mean,
              value_min  = EXCLUDED.value_min,
              value_max  = EXCLUDED.value_max,
              source     = EXCLUDED.source
        """,
        (area_id, captured_at, mean, vmin, vmax, f"cmems:{DATASET_ID}"),
    )


def sync_salinity(days_back: int = 1) -> dict:
    """Fetch modeled surface salinity for every area over the last `days_back`
    days (default just the latest day) and upsert into data_snapshots.
    """
    import numpy as np

    _ensure_auth()
    report = {"ok": 0, "empty": 0, "rows": 0, "areas": 0}

    ds = _open_surface_salinity(days_back)
    so = ds[VARIABLE]
    if "depth" in so.dims:
        so = so.isel(depth=0)          # surface
    so = so.load()                      # pull the region into memory once
    times = so["time"].values
    n_time = so.sizes.get("time", 1)

    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        for area_id, slug, w, s, e, n in _areas(conn):
            report["areas"] += 1
            cell = so.sel(
                latitude=slice(float(s), float(n)),
                longitude=slice(float(w), float(e)),
            )
            if cell.size == 0:
                report["empty"] += 1
                continue

            wrote = 0
            for ti in range(n_time):
                layer = cell.isel(time=ti) if "time" in cell.dims else cell
                vals = np.asarray(layer.values, dtype="float64")
                finite = vals[np.isfinite(vals)]
                if finite.size == 0:
                    continue
                captured = _np_to_dt(times[ti] if n_time > 1 else times[-1])
                _upsert(conn, area_id, captured,
                        float(finite.mean()), float(finite.min()), float(finite.max()))
                wrote += 1

            if wrote:
                report["ok"] += 1
                report["rows"] += wrote
            else:
                report["empty"] += 1

    log.info("CMEMS salinity sync: %s", report)
    return report
