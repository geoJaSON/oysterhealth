"""Briefing endpoints — the synthesized "lease condition" verdict per area.

  GET /api/briefings              — slug → {verdict, headline, ...} for map fill
  GET /api/areas/{slug}/briefing  — the full briefing (drivers + narrative)

Both read precomputed `area_indicators` rows where indicator='oyster_condition'
(written by api/services/synthesis.compute_all via `manage.py compute-indicators`),
so serving is a single indexed lookup — no synthesis on the request path.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session

router = APIRouter(prefix="/api", tags=["briefings"])


@router.get("/briefings")
async def briefings_all(session: AsyncSession = Depends(get_session)):
    """Latest oyster_condition verdict for every area, as a slug → summary map
    so the frontend can colour every polygon and rank areas with one fetch.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT a.slug, a.name, a.region,
                       latest.status, latest.components, latest.computed_at
                  FROM areas a
                  LEFT JOIN LATERAL (
                    SELECT status, components, computed_at
                      FROM area_indicators ai
                     WHERE ai.area_id = a.id
                       AND ai.indicator = 'oyster_condition'
                     ORDER BY ai.computed_at DESC
                     LIMIT 1
                  ) latest ON TRUE
                 ORDER BY a.slug
                """
            )
        )
    ).all()

    out: dict[str, dict] = {}
    for r in rows:
        comp = r.components or {}
        out[r.slug] = {
            "name": r.name,
            "region": r.region,
            "verdict": r.status or "unknown",
            "headline": comp.get("headline"),
            "recommendation": comp.get("recommendation"),
            "coverage": comp.get("coverage"),
            "computed_at": r.computed_at.isoformat() if r.computed_at else None,
        }
    return out


@router.get("/areas/{slug}/briefing")
async def area_briefing(slug: str, session: AsyncSession = Depends(get_session)):
    """Full briefing for one area: composite verdict, the human headline,
    a recommendation, and the interpreted driver rows.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT a.name, a.region,
                       latest.status, latest.components, latest.computed_at
                  FROM areas a
                  LEFT JOIN LATERAL (
                    SELECT status, components, computed_at
                      FROM area_indicators ai
                     WHERE ai.area_id = a.id
                       AND ai.indicator = 'oyster_condition'
                     ORDER BY ai.computed_at DESC
                     LIMIT 1
                  ) latest ON TRUE
                 WHERE a.slug = :slug
                """
            ),
            {"slug": slug},
        )
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Area not found")

    comp = row.components or {}
    return {
        "slug": slug,
        "name": row.name,
        "region": row.region,
        "verdict": row.status or "unknown",
        "headline": comp.get(
            "headline",
            "This area's briefing hasn't been computed yet — run the indicator scoring step.",
        ),
        "recommendation": comp.get("recommendation", ""),
        "coverage": comp.get("coverage", {"available": 0, "total": 0}),
        "drivers": comp.get("drivers", []),
        "forecast": comp.get("forecast"),
        "computed_at": row.computed_at.isoformat() if row.computed_at else None,
    }
