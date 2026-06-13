# OysterHealth

Coastal water intelligence for commercial shellfish harvesters on the US Gulf
of Mexico and East Coast. Aggregates satellite-derived and in-situ sensor data
into a **map-first briefing**: every bay/lease is shaded by a synthesized
*lease condition* verdict, and selecting one yields a plain-language read —
verdict, drivers, and a recommendation — with the underlying charts demoted
behind a "Trends" expander.

See **[DESIGN.md](DESIGN.md)** for the product concept and the briefing model.

> Reuses the proven data plumbing from the predecessor `waterdata` project
> (USGS / CO-OPS / ERDDAP clients, PostGIS schema, indicator engine). The deep
> data/threshold reference lives in `../waterdata/COASTA~1.MD`. OysterHealth
> replaces only the product concept — the map-first, communicate-the-finding UX.

## Stack

React + Vite + Leaflet + Recharts · FastAPI + asyncpg · PostgreSQL 16 + PostGIS
· Celery + Redis · Docker Compose.

## Local quickstart (Windows + Docker Desktop)

Prereqs: Docker Desktop, Node 20+, Python 3.12+.

```powershell
# 1. Env
Copy-Item .env.example .env        # defaults are fine for local dev

# 2. Database + Redis (DB published on host port 5433 to dodge a native
#    PostgreSQL on 5432; schema auto-runs on first volume create)
docker compose -f app/docker/docker-compose.yml up -d db redis

# 3. Backend deps (a lean runtime subset — the full requirements.txt adds
#    WeasyPrint/CMEMS/Supabase which aren't needed to run the API locally)
cd app/backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi "uvicorn[standard]" pydantic pydantic-settings python-dotenv `
  "sqlalchemy[asyncio]" asyncpg "psycopg[binary]" geoalchemy2 shapely httpx slowapi redis
#   (or: pip install -r requirements.txt  — heavier, needs GTK for WeasyPrint)

# 4. Seed reference data
cd ..\scripts
..\backend\.venv\Scripts\python seed_areas.py
..\backend\.venv\Scripts\python seed_gauges.py
..\backend\.venv\Scripts\python seed_stations.py
..\backend\.venv\Scripts\python seed_hab_examples.py    # example HAB alerts

# 5. Fetch data + compute briefings (no Celery worker needed)
cd ..\backend
.\.venv\Scripts\python manage.py backfill-usgs 90
.\.venv\Scripts\python manage.py fetch-usgs
.\.venv\Scripts\python manage.py fetch-coops
.\.venv\Scripts\python manage.py compute-indicators   # freshwater + oyster_condition briefings

# 6. Run
.\.venv\Scripts\python -m uvicorn main:app --port 8000   # API
cd ..\frontend && npm install && npm run dev             # UI → http://localhost:5273
```

`compute-indicators` is the step that produces the briefings; re-run it after
any fresh data fetch. In production these run on the Celery beat schedule.

## Key endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/briefings` | slug → verdict + headline for every area (map fill + overview) |
| `GET /api/areas/{slug}/briefing` | full synthesized briefing (verdict, drivers, recommendation) |
| `GET /api/areas/geojson` | area polygons for the map |
| `GET /api/areas/{slug}/gauges` · `/stations` · `/snapshot` · `/timeseries` | supporting data behind the "Trends" drawer |
| `GET /api/alerts/hab` | active HAB alerts (map overlay) |

## Layout

```
app/
  backend/    FastAPI app, services (usgs/coops/erddap/indicators/synthesis), manage.py
  frontend/   React + Vite; Map + always-on BriefingColumn
  db/init/    SQL bootstrap (extensions, schema, native monthly partitions)
  docker/     compose + Dockerfiles + nginx
  scripts/    one-shot seeders + partition maintenance
```
