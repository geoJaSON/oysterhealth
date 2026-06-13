"""Server-rendered PDF briefing report.

Renders the same synthesized briefing the UI shows into a clean one-page PDF via
WeasyPrint. WeasyPrint needs native GTK libs (cairo/pango); the Docker backend
image ships them, but a bare Windows host venv won't — so the import is lazy and
a missing renderer yields HTTP 503, which the frontend treats as a signal to
fall back to browser print.
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session

router = APIRouter(prefix="/api", tags=["reports"])

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

VERDICT_COLOR = {"good": "#2fb170", "caution": "#e6a23c", "poor": "#e2533f", "unknown": "#64748b"}
VERDICT_LABEL = {"good": "Good", "caution": "Caution", "poor": "Poor", "unknown": "No data"}
REGION_LABEL = {"gulf": "Gulf of Mexico", "east_coast": "US East Coast"}


def _render_html(name: str, region: str, verdict: str, comp: dict, computed_at: str | None) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("report.html")
    drivers = [
        {**d, "color": VERDICT_COLOR.get(d.get("status"), "#64748b")}
        for d in comp.get("drivers", [])
    ]
    cov = comp.get("coverage") or {}
    return tmpl.render(
        area_name=name,
        region_label=REGION_LABEL.get(region, region),
        verdict_color=VERDICT_COLOR.get(verdict, "#64748b"),
        verdict_label=VERDICT_LABEL.get(verdict, verdict),
        headline=comp.get("headline", ""),
        recommendation=comp.get("recommendation", ""),
        drivers=drivers,
        coverage_available=cov.get("available", 0),
        coverage_total=cov.get("total", 0),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        computed_at=computed_at,
    )


@router.get("/areas/{slug}/report.pdf")
async def area_report_pdf(slug: str, session: AsyncSession = Depends(get_session)):
    row = (
        await session.execute(
            text(
                """
                SELECT a.name, a.region, latest.status, latest.components, latest.computed_at
                  FROM areas a
                  LEFT JOIN LATERAL (
                    SELECT status, components, computed_at
                      FROM area_indicators ai
                     WHERE ai.area_id = a.id AND ai.indicator = 'oyster_condition'
                     ORDER BY ai.computed_at DESC LIMIT 1
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
    if not comp:
        raise HTTPException(status_code=409, detail="Briefing not computed yet — run compute-indicators")

    computed_at = row.computed_at.strftime("%Y-%m-%d %H:%M UTC") if row.computed_at else None
    html = _render_html(row.name, row.region, row.status or "unknown", comp, computed_at)

    # Lazy import: WeasyPrint (and its GTK native libs) may be absent on a dev
    # host. 503 → the frontend falls back to browser print.
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:  # OSError = GTK libs missing
        raise HTTPException(
            status_code=503,
            detail=f"Server PDF renderer unavailable on this host ({exc}). Use browser print.",
        )

    pdf = HTML(string=html).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="oysterhealth-{slug}.pdf"'},
    )
