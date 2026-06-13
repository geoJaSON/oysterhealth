"""NOAA CO-OPS station endpoints.

  GET /api/areas/{slug}/stations              — stations near the area + latest readings
  GET /api/stations/{station_id}/timeseries   — history for one (station, variable)

The plan calls out 15 km from polygon centroid as the threshold beyond which
in-situ readings can't really be trusted to represent the area. The
`distance_warning` flag in the response surfaces that to the UI.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session

router = APIRouter(prefix="/api", tags=["stations"])

StationVariable = Literal["water_temperature", "salinity", "water_level"]

UNITS_BY_VARIABLE = {
    "water_temperature": "degree_C",
    "salinity": "psu",
    "water_level": "m",
}

DISTANCE_WARNING_M = 15_000   # plan §6 — beyond this the station can't represent the area
MAX_STATIONS = 5


@router.get("/areas/{slug}/stations")
async def stations_for_area(slug: str, session: AsyncSession = Depends(get_session)):
    """Return the nearest CO-OPS stations to this area's polygon centroid,
    each with its most recent reading per advertised variable. Sorted by
    distance, ascending.
    """
    nearby_rows = (
        await session.execute(
            text(
                """
                SELECT
                  s.id,
                  s.station_id,
                  s.name,
                  s.lat,
                  s.lon,
                  s.variables,
                  ST_DistanceSphere(
                    ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326),
                    ST_Centroid(a.geom)
                  ) AS distance_m
                FROM stations s, areas a
                WHERE a.slug = :slug
                ORDER BY distance_m ASC
                LIMIT :lim
                """
            ),
            {"slug": slug, "lim": MAX_STATIONS},
        )
    ).all()
    if not nearby_rows:
        # `slug` may be valid but stations table empty — return empty list, not 404
        return []

    station_uuids = [r.id for r in nearby_rows]
    latest_rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (station_id, variable)
                       station_id, variable, recorded_at, value, unit
                  FROM station_readings
                 WHERE station_id = ANY(:ids)
                 ORDER BY station_id, variable, recorded_at DESC
                """
            ),
            {"ids": station_uuids},
        )
    ).all()
    latest_by_station: dict[str, dict[str, dict]] = {}
    for r in latest_rows:
        latest_by_station.setdefault(str(r.station_id), {})[r.variable] = {
            "value": float(r.value),
            "recorded_at": r.recorded_at.isoformat(),
            "unit": r.unit,
        }

    return [
        {
            "station_id": r.station_id,
            "name": r.name,
            "lat": float(r.lat),
            "lon": float(r.lon),
            "variables": list(r.variables or []),
            "distance_m": float(r.distance_m),
            "distance_warning": float(r.distance_m) > DISTANCE_WARNING_M,
            "latest": latest_by_station.get(str(r.id), {}),
        }
        for r in nearby_rows
    ]


@router.get("/stations/{station_id}/timeseries")
async def station_timeseries(
    station_id: str,
    variable: StationVariable = Query(...),
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """Historical readings for one (station, variable) pair, oldest first."""
    station = (
        await session.execute(
            text("SELECT id, name FROM stations WHERE station_id = :s"),
            {"s": station_id},
        )
    ).first()
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await session.execute(
            text(
                """
                SELECT recorded_at, value
                  FROM station_readings
                 WHERE station_id = :sid
                   AND variable   = :var
                   AND recorded_at >= :since
                 ORDER BY recorded_at ASC
                """
            ),
            {"sid": station.id, "var": variable, "since": since},
        )
    ).all()

    return {
        "station_id": station_id,
        "name": station.name,
        "variable": variable,
        "units": UNITS_BY_VARIABLE.get(variable),
        "days": days,
        "points": [
            {"t": r.recorded_at.isoformat(), "value": float(r.value)}
            for r in rows
        ],
    }
