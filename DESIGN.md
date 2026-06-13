# OysterHealth — Design

## What this is

A desktop web app that aggregates satellite-derived and in-situ sensor data
into a clear, actionable picture of water conditions for commercial shellfish
harvesters working the US Gulf of Mexico and East Coast. The differentiators
are the **oyster-drill risk indicator**, **freshwater-intrusion tracking tied
to upstream river discharge**, and the combination of **predefined named areas
with user-drawn custom polygons**.

## The north star (and the failure it corrects)

OysterHealth is a deliberate rebuild of an earlier attempt (`waterdata`). That
version collected good data but presented it as a **wall of charts** — five
stacked Recharts panels beside a map that was just a backdrop. It never
answered the only question a working harvester actually has:

> *"What's happening on my lease right now, and what should I do about it?"*

So the governing rule here is:

> **The map is the application, and every output answers "so what?" — in plain
> language — before it shows a number.**

Concretely:

1. **Map-first, always.** A full map split with an always-visible briefing
   column (~60/40). Every bay/lease polygon is shaded by its *synthesized*
   condition, so you read the whole coast at a glance.
2. **Synthesis over raw plots.** Each area resolves to one composite **verdict**
   — `Good / Caution / Poor` — with a one-paragraph human headline, a short
   recommendation, and a row of *interpreted* drivers. Charts are demoted
   behind a "Trends & supporting data" expander; they are evidence, not the
   headline.
3. **Differentiators are interpreted, not just plotted.** Drill risk is a
   `Low / Watch / High` read with the *why*; freshwater intrusion is a
   directional state (`Active / Receding / Drought / Normal`) tied to the named
   upstream river.
4. **Honest about coverage.** Each driver degrades gracefully to "No data" and
   the briefing reports how many sources are reporting. The app never implies
   certainty it doesn't have. Provisional signals are tagged `est`.

## Layout (chosen)

```
┌───────────────────────────────────────────────────────────┐
│ 🦪 OysterHealth                        Overlay: [Off|SST|…] │  top bar
├──────────────────────────────────┬────────────────────────┤
│                                   │ Barataria Bay          │
│   MAP (~60%)                      │ ● CAUTION   updated 2h │
│   bays shaded by verdict          │ A Mississippi pulse is │
│   click → briefing                │ dropping salinity…     │
│   HAB + ERDDAP WMS overlays       │ What to do: …          │
│   legend bottom-left              │ ▸ Drill risk   Low ↓   │
│                                   │ ▸ Freshwater   Active  │
│                                   │ ▸ Salinity     8.1 psu │
│                                   │ ▸ Water temp   24°C    │
│                                   │ ▸ Turbidity    Clear   │
│                                   │ ▸ HAB          None    │
│                                   │ [Trends ▾] [Export PDF]│
└──────────────────────────────────┴────────────────────────┘
```

With no area selected, the briefing column is an **overview**: every area
ranked worst-first ("3 of 24 areas need attention").

## Briefing synthesis model

Computed by `app/backend/api/services/synthesis.py`, persisted as an
`area_indicators` row (`indicator='oyster_condition'`, full breakdown in
`components` JSONB), and served precomputed by `api/routes/briefings.py`. This
matches the existing scheduled-scoring pattern, so the request path is a single
indexed lookup.

**Drivers** (each → `good | caution | poor | unknown`, favorability *for the
harvester*):

| Driver | Source | Notes |
|---|---|---|
| Oyster drill risk | salinity + temp + freshwater state | provisional model; see below |
| Freshwater intrusion | `area_indicators` (discharge vs 30-day mean) | tied to the named upstream river |
| Salinity | nearest CO-OPS station (≤15 km = measured) | oyster-suitable band 5–30 psu |
| Water temperature | nearest CO-OPS station | heat-stress flag ≥32 °C |
| Turbidity | ERDDAP Kd₄₉₀ snapshot | murky > 0.5 m⁻¹ |
| Harmful algal bloom | `ST_Intersects` on `hab_alerts` | closure/warning dominate |

**Composite verdict** = worst driver severity (any `poor` → Poor; any
`caution` → Caution; else Good; no data → No data).

**Oyster-drill model (provisional).** Drills can't tolerate low salinity;
sustained high salinity + warm water drives peak predation. Freshwater pulses
suppress them. The **WATCH-rebound** state is modeled explicitly: when a
freshwater event is *receding* and water is warm, drills are predicted to
return even if the current salinity reading is still low. Confidence is
`estimated` until CMEMS modeled salinity lands (Phase 2); a nearby station
salinity reading promotes it to `measured`.

## Data sources (reused from the predecessor's plumbing)

- **USGS NWIS** — river discharge / stage (implemented).
- **NOAA CO-OPS** — water temp, salinity, water level (implemented).
- **NOAA CoastWatch ERDDAP** — SST, chlorophyll, turbidity (Kd₄₉₀) as DB
  snapshots + map WMS tiles. *ERDDAP WMS only serves EPSG:4326* — the Leaflet
  layers set `crs: L.CRS.EPSG4326` accordingly.
- **NASA GIBS / HAB / EPA WQP / Copernicus CMEMS** — planned / Phase 2.

The deep reference for data sources, gauge lists, named-area definitions, and
indicator thresholds lives in the predecessor plan at
`../waterdata/COASTA~1.MD`. Treat it as the data appendix; treat **this**
document as the product concept.

## Provisional / Phase 2

- CMEMS modeled salinity → upgrades drill risk from `estimated` to a real
  field, and fills salinity where no CO-OPS station is near.
- Live HAB ingestion (currently seeded example alerts).
- Server-side WeasyPrint reports (today "Export PDF" is a print-stylesheet
  render of the briefing — already briefing-shaped, not a chart dump).
- User auth (Supabase, wired but inactive) → saved custom-polygon leases.
- Real coastline polygons replacing the approximate area bounding boxes
  (slugs are stable, so `areas.geom` can be updated in place).

## Stack

React + Vite + Leaflet + Recharts · FastAPI + asyncpg · PostgreSQL 16 + PostGIS
· Celery + Redis · Docker Compose. Identical to the predecessor's proven,
debugged stack — only the product concept changed.
