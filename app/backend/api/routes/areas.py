"""Area endpoints — metadata, latest variable snapshot, per-variable timeseries.

Indicator scoring lands in Phase 2.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.areas import AreaOut, CustomAreaIn
from database import get_session
from main_limiter import limiter

Variable = Literal["sst", "chlorophyll", "turbidity", "cdom", "salinity"]

router = APIRouter(prefix="/api/areas", tags=["areas"])


@router.get("/geojson")
async def areas_geojson(session: AsyncSession = Depends(get_session)):
    """All areas as a GeoJSON FeatureCollection for map overlay.

    Returns the polygon + bounds + slug/region per area. Declared above the
    `/{slug}` routes so FastAPI doesn't match `geojson` as a slug.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT
                  slug, name, region, area_type,
                  ST_AsGeoJSON(geom)::json AS geometry,
                  ST_XMin(geom) AS w, ST_YMin(geom) AS s,
                  ST_XMax(geom) AS e, ST_YMax(geom) AS n
                FROM areas
                ORDER BY slug
                """
            )
        )
    ).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": r.geometry,
                "properties": {
                    "slug": r.slug,
                    "name": r.name,
                    "region": r.region,
                    "area_type": r.area_type,
                    "bbox": [float(r.w), float(r.s), float(r.e), float(r.n)],
                },
            }
            for r in rows
        ],
    }


@router.get("", response_model=list[AreaOut])
async def list_areas(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        text(
            """
            SELECT id, name, slug, region, area_type, description, linked_gauges
              FROM areas
             ORDER BY region, name
            """
        )
    )
    return [
        AreaOut(
            id=str(r.id),
            name=r.name,
            slug=r.slug,
            region=r.region,
            area_type=r.area_type,
            description=r.description,
            linked_gauges=list(r.linked_gauges or []),
        )
        for r in rows
    ]


@router.get("/{slug}", response_model=AreaOut)
async def get_area(slug: str, session: AsyncSession = Depends(get_session)):
    row = (
        await session.execute(
            text(
                """
                SELECT id, name, slug, region, area_type, description, linked_gauges
                  FROM areas
                 WHERE slug = :slug
                """
            ),
            {"slug": slug},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Area not found")
    return AreaOut(
        id=str(row.id),
        name=row.name,
        slug=row.slug,
        region=row.region,
        area_type=row.area_type,
        description=row.description,
        linked_gauges=list(row.linked_gauges or []),
    )


@router.post("/custom", status_code=201)
@limiter.limit("10/hour")
async def save_custom_area(
    request: Request,
    payload: CustomAreaIn,
    session: AsyncSession = Depends(get_session),
):
    """Anonymous custom polygon save. Geometry is validated in the Pydantic
    schema and again at the DB layer via ST_MakeValid(ST_Force2D(ST_ForcePolygonCCW(...)))
    to enforce the right-hand rule for PostGIS.
    """
    import json

    inserted = await session.execute(
        text(
            """
            INSERT INTO areas (name, slug, region, area_type, geom)
            VALUES (
              :name,
              lower(regexp_replace(:name, '[^a-zA-Z0-9]+', '-', 'g'))
                || '-' || substring(gen_random_uuid()::text, 1, 8),
              'gulf',
              'custom',
              ST_MakeValid(ST_Force2D(ST_ForcePolygonCCW(
                ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326)
              )))
            )
            RETURNING id, slug
            """
        ),
        {"name": payload.name, "geojson": json.dumps(payload.geojson)},
    )
    row = inserted.first()
    await session.commit()
    return {"id": str(row.id), "slug": row.slug}


# ---------------------------------------------------------------------------
# Variable snapshot + timeseries (ERDDAP-fed)
# ---------------------------------------------------------------------------

UNITS_BY_VARIABLE = {
    "sst":         "degree_C",
    "chlorophyll": "mg m-3",
    "turbidity":   "m-1",
    "cdom":        "index",
    "salinity":    "psu",
}

# Plan P1: anomaly threshold = |z-score| > 1.5σ from the rolling baseline.
ANOMALY_Z = 1.5
BASELINE_WINDOW_DAYS = 30


async def _area_id_or_404(session: AsyncSession, slug: str) -> str:
    row = (
        await session.execute(
            text("SELECT id FROM areas WHERE slug = :slug"),
            {"slug": slug},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Area not found")
    return row.id


@router.get("/{slug}/snapshot")
async def area_snapshot(slug: str, session: AsyncSession = Depends(get_session)):
    """Latest data_snapshots value per variable for this area, plus a
    rolling-baseline anomaly flag (|z-score| > 1.5σ over 30 days).
    """
    area_id = await _area_id_or_404(session, slug)
    rows = (
        await session.execute(
            text(
                f"""
                WITH baseline AS (
                  SELECT variable,
                         avg(value_mean)         AS baseline_mean,
                         stddev_samp(value_mean) AS baseline_std,
                         count(*)                AS baseline_n
                    FROM data_snapshots
                   WHERE area_id = :aid
                     AND captured_at >= now() - interval '{BASELINE_WINDOW_DAYS} days'
                     AND value_mean IS NOT NULL
                   GROUP BY variable
                ),
                latest AS (
                  SELECT DISTINCT ON (variable)
                         variable, captured_at, value_mean, value_min, value_max, source
                    FROM data_snapshots
                   WHERE area_id = :aid
                   ORDER BY variable, captured_at DESC
                )
                SELECT latest.*, baseline.baseline_mean, baseline.baseline_std, baseline.baseline_n
                  FROM latest LEFT JOIN baseline USING (variable)
                """
            ),
            {"aid": area_id},
        )
    ).all()

    def _anomaly_payload(r) -> dict:
        mean = float(r.value_mean) if r.value_mean is not None else None
        baseline_mean = float(r.baseline_mean) if r.baseline_mean is not None else None
        baseline_std = float(r.baseline_std) if r.baseline_std is not None else None
        z = None
        direction = None
        is_anomaly = False
        # Need enough baseline samples and a non-zero σ to compute z.
        if (mean is not None and baseline_mean is not None
                and baseline_std is not None and baseline_std > 0
                and r.baseline_n is not None and r.baseline_n >= 3):
            z = (mean - baseline_mean) / baseline_std
            if abs(z) > ANOMALY_Z:
                is_anomaly = True
                direction = "high" if z > 0 else "low"
        return {
            "captured_at": r.captured_at.isoformat(),
            "value_mean": mean,
            "value_min":  float(r.value_min)  if r.value_min  is not None else None,
            "value_max":  float(r.value_max)  if r.value_max  is not None else None,
            "units": UNITS_BY_VARIABLE.get(r.variable),
            "source": r.source,
            "baseline_mean": baseline_mean,
            "baseline_std":  baseline_std,
            "baseline_n":    int(r.baseline_n) if r.baseline_n is not None else 0,
            "z_score": z,
            "is_anomaly": is_anomaly,
            "anomaly_direction": direction,
            "anomaly_threshold_z": ANOMALY_Z,
        }

    return {
        "slug": slug,
        "variables": {r.variable: _anomaly_payload(r) for r in rows},
    }


@router.get("/{slug}/timeseries")
async def area_timeseries(
    slug: str,
    variable: Variable = Query(..., description="sst | chlorophyll | turbidity | cdom | salinity"),
    days: int = Query(default=30, ge=1, le=730),
    session: AsyncSession = Depends(get_session),
):
    """Historical data_snapshots for one variable, oldest-first."""
    area_id = await _area_id_or_404(session, slug)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await session.execute(
            text(
                """
                SELECT captured_at, value_mean, value_min, value_max
                  FROM data_snapshots
                 WHERE area_id  = :aid
                   AND variable = :var
                   AND captured_at >= :since
                 ORDER BY captured_at ASC
                """
            ),
            {"aid": area_id, "var": variable, "since": since},
        )
    ).all()
    return {
        "slug": slug,
        "variable": variable,
        "units": UNITS_BY_VARIABLE.get(variable),
        "days": days,
        "points": [
            {
                "t": r.captured_at.isoformat(),
                "value_mean": float(r.value_mean) if r.value_mean is not None else None,
                "value_min":  float(r.value_min)  if r.value_min  is not None else None,
                "value_max":  float(r.value_max)  if r.value_max  is not None else None,
            }
            for r in rows
        ],
    }
