"""HAB (Harmful Algal Bloom) alert endpoints.

  GET /api/alerts/hab            — every non-expired alert as GeoJSON (map overlay)
  GET /api/areas/{slug}/hab      — alerts whose geom intersects this area

Data ingest is still a Phase 2 TODO — see api/workers/hab.py. The example
rows from app/scripts/seed_hab_examples.py give the UI realistic data to
render until a live fetcher lands.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session

router = APIRouter(prefix="/api", tags=["alerts"])


def _row_to_geojson_feature(r) -> dict:
    return {
        "type": "Feature",
        "geometry": r.geometry,
        "properties": {
            "id": str(r.id),
            "region": r.region,
            "alert_level": r.alert_level,
            "species": r.species,
            "description": r.description,
            "issued_at": r.issued_at.isoformat() if r.issued_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        },
    }


@router.get("/alerts/hab")
async def hab_alerts(session: AsyncSession = Depends(get_session)):
    """All currently active HAB alerts as a GeoJSON FeatureCollection.

    "Active" = expires_at IS NULL OR expires_at > now(). Cheap query: <100 rows
    typical, GIST index already on hab_alerts.geom.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id, region, alert_level, species, description,
                       issued_at, expires_at,
                       ST_AsGeoJSON(geom)::json AS geometry
                  FROM hab_alerts
                 WHERE geom IS NOT NULL
                   AND (expires_at IS NULL OR expires_at > now())
                 ORDER BY issued_at DESC
                """
            )
        )
    ).all()
    return {
        "type": "FeatureCollection",
        "features": [_row_to_geojson_feature(r) for r in rows],
    }


@router.get("/areas/{slug}/hab")
async def area_hab(slug: str, session: AsyncSession = Depends(get_session)):
    """Active HAB alerts whose geom intersects this area's polygon.

    Uses ST_Intersects with the GIST indexes on both `hab_alerts.geom` and
    `areas.geom`.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT h.id, h.region, h.alert_level, h.species, h.description,
                       h.issued_at, h.expires_at,
                       ST_AsGeoJSON(h.geom)::json AS geometry
                  FROM hab_alerts h
                  JOIN areas a ON ST_Intersects(h.geom, a.geom)
                 WHERE a.slug = :slug
                   AND h.geom IS NOT NULL
                   AND (h.expires_at IS NULL OR h.expires_at > now())
                 ORDER BY h.issued_at DESC
                """
            ),
            {"slug": slug},
        )
    ).all()
    if not rows:
        exists = (
            await session.execute(
                text("SELECT 1 FROM areas WHERE slug = :slug"),
                {"slug": slug},
            )
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Area not found")
    return {"slug": slug, "alerts": [_row_to_geojson_feature(r)["properties"] | {"geometry": _row_to_geojson_feature(r)["geometry"]} for r in rows]}
