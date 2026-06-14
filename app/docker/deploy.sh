#!/usr/bin/env bash
# First-time production bring-up on a VPS (Docker already installed).
# Run from the repo root:  bash app/docker/deploy.sh
set -euo pipefail

COMPOSE="docker compose -f app/docker/docker-compose.prod.yml"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f .env ]; then
  echo "ERROR: no .env at repo root. Copy .env.prod.example to .env and fill it in first."
  exit 1
fi

# Fail closed on TLS: an empty SITE_ADDRESS makes Caddy silently serve plain HTTP.
SITE=$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2- || true)
if [ -z "$SITE" ]; then
  echo "ERROR: SITE_ADDRESS is empty in .env — set your domain (for TLS) or ':80' (HTTP-only testing)."
  exit 1
elif [ "$SITE" = ":80" ]; then
  echo "WARNING: SITE_ADDRESS=:80 — serving plain HTTP with no TLS (testing mode)."
fi

echo ">> Building images (backend + web)..."
$COMPOSE build

echo ">> Starting db + redis (waiting for healthy)..."
$COMPOSE up -d --wait db redis

echo ">> Applying schema migrations..."
$COMPOSE run --rm backend python manage.py migrate

echo ">> Seeding reference data (areas/gauges/stations/HAB examples)..."
# The seed scripts live in app/scripts (not baked into the backend image), so
# bind-mount them for this one-time bootstrap. zone_geoms.json is committed, so
# the real zone geometry loads without re-fetching.
$COMPOSE run --rm -v "$REPO_ROOT/app/scripts:/app/scripts" backend bash -lc "\
  cd /app/scripts && \
  python seed_areas.py && python seed_gauges.py && python seed_stations.py && python seed_hab_examples.py"

echo ">> Initial data fetch + briefing compute (a few minutes)..."
# Includes the satellite (ERDDAP) + modeled-salinity (CMEMS) fetches: those write
# per-area data_snapshots, and the salinity driver falls back to modeled salinity
# when no CO-OPS station reports it — so without these, briefings launch with
# "No data" salinity/turbidity until the first scheduled beat run. CMEMS is
# non-fatal (needs creds; skip cleanly if absent).
$COMPOSE run --rm backend bash -lc "\
  python manage.py backfill-usgs 90 && \
  python manage.py fetch-usgs && \
  python manage.py fetch-coops && \
  python manage.py fetch-nwm && \
  python manage.py fetch-erddap && \
  { python manage.py fetch-cmems || echo 'CMEMS skipped — set CMEMS creds in .env for modeled salinity'; } && \
  python manage.py compute-indicators"

echo ">> Starting the app (backend, workers, beat, web)..."
$COMPOSE up -d

echo
echo ">> Done. TLS: if the domain's DNS A record already points here, Caddy has"
echo "   issued a cert. If you set DNS just now, wait for it to propagate then run:"
echo "       $COMPOSE restart web"
echo "   From now on the Celery beat schedule keeps data fresh — no manual fetches."
$COMPOSE ps
