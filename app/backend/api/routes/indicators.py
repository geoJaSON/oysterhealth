"""Indicator endpoints.

  GET /api/areas/{slug}/indicators      — every indicator for one area (latest)
  GET /api/indicators/freshwater-intrusion — per-area status map for the whole map view
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session

router = APIRouter(prefix="/api", tags=["indicators"])


def _row_to_dict(row) -> dict:
    return {
        "indicator": row.indicator,
        "status": row.status,
        "score": float(row.score) if row.score is not None else None,
        "computed_at": row.computed_at.isoformat(),
        "components": row.components,
    }


@router.get("/areas/{slug}/indicators")
async def area_indicators(slug: str, session: AsyncSession = Depends(get_session)):
    """Latest row per indicator for this area. Used by the right-rail status badges."""
    rows = (
        await session.execute(
            text(
                """
                WITH a AS (SELECT id FROM areas WHERE slug = :slug)
                SELECT DISTINCT ON (ai.indicator)
                       ai.indicator, ai.status, ai.score, ai.computed_at, ai.components
                  FROM area_indicators ai
                  JOIN a ON a.id = ai.area_id
                 ORDER BY ai.indicator, ai.computed_at DESC
                """
            ),
            {"slug": slug},
        )
    ).all()

    # Confirm the slug exists even when no indicators are stored yet — return
    # 404 vs an empty list so the frontend can tell "no such area" from
    # "indicators haven't been computed yet".
    if not rows:
        exists = (
            await session.execute(
                text("SELECT 1 FROM areas WHERE slug = :slug"),
                {"slug": slug},
            )
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Area not found")

    return {"slug": slug, "indicators": [_row_to_dict(r) for r in rows]}


@router.get("/indicators/freshwater-intrusion")
async def freshwater_intrusion_all(session: AsyncSession = Depends(get_session)):
    """Latest freshwater-intrusion status for every area.

    Returned as a slug → {status, score, computed_at} map so the frontend can
    colour each polygon on the map with a single fetch.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT a.slug,
                       latest.status,
                       latest.score,
                       latest.computed_at
                  FROM areas a
                  LEFT JOIN LATERAL (
                    SELECT status, score, computed_at
                      FROM area_indicators ai
                     WHERE ai.area_id = a.id
                       AND ai.indicator = 'freshwater_intrusion'
                     ORDER BY ai.computed_at DESC
                     LIMIT 1
                  ) latest ON TRUE
                """
            )
        )
    ).all()
    return {
        r.slug: {
            "status": r.status,
            "score": float(r.score) if r.score is not None else None,
            "computed_at": r.computed_at.isoformat() if r.computed_at else None,
        }
        for r in rows
    }
