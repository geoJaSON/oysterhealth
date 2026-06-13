"""USGS gauge endpoints.

  GET /api/areas/{slug}/gauges        — gauges linked to an area + latest reading
  GET /api/gauges/{site_no}/timeseries — historical readings for a single gauge
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session

router = APIRouter(prefix="/api", tags=["gauges"])


@router.get("/areas/{slug}/gauges")
async def gauges_for_area(slug: str, session: AsyncSession = Depends(get_session)):
    """Returns the gauges linked to this area plus each gauge's most recent
    (non-null) discharge and stage values. `linked_gauges` on the `areas`
    table is the source of truth for the link.
    """
    rows = (
        await session.execute(
            text(
                """
                WITH linked AS (
                  SELECT g.id, g.site_no, g.name, g.river, g.lat, g.lon
                    FROM areas a
                    JOIN usgs_gauges g ON g.site_no = ANY(a.linked_gauges)
                   WHERE a.slug = :slug
                ),
                latest_q AS (
                  SELECT DISTINCT ON (gauge_id) gauge_id, recorded_at, discharge_cfs
                    FROM gauge_readings
                   WHERE discharge_cfs IS NOT NULL
                   ORDER BY gauge_id, recorded_at DESC
                ),
                latest_s AS (
                  SELECT DISTINCT ON (gauge_id) gauge_id, recorded_at, stage_ft
                    FROM gauge_readings
                   WHERE stage_ft IS NOT NULL
                   ORDER BY gauge_id, recorded_at DESC
                )
                SELECT linked.site_no, linked.name, linked.river, linked.lat, linked.lon,
                       latest_q.discharge_cfs, latest_q.recorded_at AS discharge_at,
                       latest_s.stage_ft,      latest_s.recorded_at AS stage_at
                  FROM linked
                  LEFT JOIN latest_q ON latest_q.gauge_id = linked.id
                  LEFT JOIN latest_s ON latest_s.gauge_id = linked.id
                 ORDER BY linked.river NULLS LAST, linked.name
                """
            ),
            {"slug": slug},
        )
    ).all()

    return [
        {
            "site_no": r.site_no,
            "name": r.name,
            "river": r.river,
            "lat": float(r.lat),
            "lon": float(r.lon),
            "latest_discharge_cfs": float(r.discharge_cfs) if r.discharge_cfs is not None else None,
            "latest_discharge_at": r.discharge_at.isoformat() if r.discharge_at else None,
            "latest_stage_ft": float(r.stage_ft) if r.stage_ft is not None else None,
            "latest_stage_at": r.stage_at.isoformat() if r.stage_at else None,
        }
        for r in rows
    ]


@router.get("/gauges/{site_no}/timeseries")
async def gauge_timeseries(
    site_no: str,
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """Historical discharge + stage for a single gauge.

    `days` selects the lookback window; default 30 days matches the
    freshwater-intrusion indicator's 30-day baseline.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    gauge = (
        await session.execute(
            text("SELECT id, name, river FROM usgs_gauges WHERE site_no = :s"),
            {"s": site_no},
        )
    ).first()
    if gauge is None:
        raise HTTPException(status_code=404, detail="Gauge not found")

    rows = (
        await session.execute(
            text(
                """
                SELECT recorded_at, discharge_cfs, stage_ft
                  FROM gauge_readings
                 WHERE gauge_id = :gid
                   AND recorded_at >= :since
                 ORDER BY recorded_at ASC
                """
            ),
            {"gid": gauge.id, "since": since},
        )
    ).all()

    return {
        "site_no": site_no,
        "name": gauge.name,
        "river": gauge.river,
        "days": days,
        "points": [
            {
                "t": r.recorded_at.isoformat(),
                "discharge_cfs": float(r.discharge_cfs) if r.discharge_cfs is not None else None,
                "stage_ft": float(r.stage_ft) if r.stage_ft is not None else None,
            }
            for r in rows
        ],
    }
