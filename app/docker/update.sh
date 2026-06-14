#!/usr/bin/env bash
# Deploy a new version on the VPS. Run from the repo root:  bash app/docker/update.sh
# Pulls latest code, rebuilds images, applies any new migrations, restarts.
set -euo pipefail

COMPOSE="docker compose -f app/docker/docker-compose.prod.yml"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# Sanity: SITE_ADDRESS must be set (an empty value silently drops TLS on rebuild).
SITE=$(grep -E '^SITE_ADDRESS=' .env 2>/dev/null | cut -d= -f2- || true)
[ -z "$SITE" ] && { echo "ERROR: SITE_ADDRESS is empty in .env"; exit 1; }

echo ">> Pulling latest code..."
git pull --ff-only

echo ">> Rebuilding images..."
$COMPOSE build

echo ">> Applying any new migrations..."
$COMPOSE run --rm backend python manage.py migrate

echo ">> Restarting services..."
$COMPOSE up -d

# If a release adds/changes areas or indicators and you want them live immediately
# (rather than waiting for the next scheduled compute), uncomment:
# $COMPOSE run --rm -v "$REPO_ROOT/app/scripts:/app/scripts" backend bash -lc \
#   "cd /app/scripts && python seed_areas.py && python seed_gauges.py"
# $COMPOSE run --rm backend python manage.py compute-indicators

echo ">> Done."
$COMPOSE ps
