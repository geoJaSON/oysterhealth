# Deploying OysterHealth to a VPS

Production runs entirely in Docker on a single VPS. This guide covers first
deploy, ongoing updates, and how the data keeps itself current.

## What runs

`app/docker/docker-compose.prod.yml` defines six services on a private Docker
network; **only the web tier is exposed to the internet**:

| Service | Role | Exposed? |
|---|---|---|
| `web` | Caddy — serves the built SPA + reverse-proxies `/api` to the backend, **automatic HTTPS** | **80 / 443** |
| `backend` | FastAPI (uvicorn, 2 workers) | internal only |
| `worker_io` | Celery worker — USGS / NOAA / NWM / HAB fetches | internal |
| `worker_geo` | Celery worker — satellite / CMEMS / scoring | internal |
| `beat` | Celery beat — the schedule that **keeps data fresh** | internal |
| `db` | PostGIS 16 (named volume `pgdata`) | internal |
| `redis` | Celery broker (named volume `redisdata`) | internal |

> The **dev** compose (`docker-compose.yml`) publishes Postgres/Redis/backend to
> the host for local work — never use it on a public VPS (an exposed
> unauthenticated Redis is a remote-code-execution risk). Use the **prod** compose.

## "Updates" = two separate things

1. **Data freshness** — automatic. `beat` + the two workers run the schedule
   (USGS & NOAA hourly, NWM & satellite every 6 h, `compute-indicators` daily —
   which recomputes the briefings/verdicts — cleanup weekly). Nothing manual.
2. **Code / schema deploys** — `bash app/docker/update.sh` (git pull → rebuild →
   `migrate` → restart). See *Updating*.

---

## First deploy

Prereqs: a VPS with Docker + the compose plugin, a domain, and ports 80/443 open.

```bash
git clone <your-repo-url> oysterhealth && cd oysterhealth

# 1. Secrets + config (CONTAINER networking values — see the template)
cp .env.prod.example .env
nano .env          # set POSTGRES_PASSWORD, SITE_ADDRESS=<domain>, CORS_ORIGINS=https://<domain>
                   # leave POSTGRES_HOST=db and POSTGRES_PORT=5432 as-is (container
                   # networking) — copying a dev .env's localhost/5433 is the #1 cause of a 502.
                   # Don't set VITE_API_BASE_URL — the SPA is built to call /api same-origin.

# 2. Point DNS: an A record for <domain> → this VPS's IP (so Caddy can get a cert)

# 3. One command does the rest: build, start db/redis, migrate, seed, initial
#    fetch + compute, then start the app.
bash app/docker/deploy.sh
```

Caddy issues a Let's Encrypt certificate automatically once the domain resolves
to the host. To test before DNS is ready, set `SITE_ADDRESS=:80` for plain HTTP.

> **Note:** to skip the TLS step entirely (HTTP only), set `SITE_ADDRESS=:80`. For
> a real launch use the domain so HTTPS is automatic.

---

## Updating

### Code
```bash
bash app/docker/update.sh      # git pull, rebuild, migrate, restart
```

### Database schema
`db/init/*.sql` runs **only on a fresh volume**, so schema changes to a live DB
go through **migrations**, applied on every `update.sh` (and `deploy.sh`):

- Add a file `app/backend/migrations/NNNN_description.sql` (next number).
- Make it **idempotent** (`CREATE TABLE IF NOT EXISTS …`, `ALTER TABLE … ADD
  COLUMN IF NOT EXISTS …`) so re-runs are safe.
- `python manage.py migrate` tracks applied files in a `schema_migrations` table.

(The forecast tables added in 2026-06 — `nwm_forecasts`, `usgs_gauges.nwm_reach_id`
— are `migrations/0001_forecast_schema.sql`.)

### Areas / zone geometry
Editing which areas exist or their shapes is covered in
[app/scripts/AREAS.md](app/scripts/AREAS.md). After editing, re-run the seed (and
`build_zone_geoms.py` if you changed zone geometry) — `update.sh` has a commented
block for doing this on deploy. `zone_geoms.json` is committed, so the server
doesn't re-fetch geometry unless you regenerate it.

---

## Verifying it's healthy

```bash
docker compose -f app/docker/docker-compose.prod.yml ps        # all "Up"/"healthy"
docker compose -f app/docker/docker-compose.prod.yml logs -f beat worker_io

# Data is advancing? computed_at should move after the daily 06:00 UTC run:
docker exec oyster_db psql -U oyster_user -d oyster_health -c \
  "SELECT indicator, max(computed_at) FROM area_indicators GROUP BY indicator;"
```

The schedule lives in `app/backend/celery_app.py` (`beat_schedule`); adjust cadences
there. The stale-briefing fix means the daily `compute-indicators` recomputes the
oyster-condition briefings, not just the freshwater indicator. The weekly maintenance
task rolls the `data_snapshots` monthly partition window forward (it does **not** purge
data), so **beat must stay running** — otherwise an insert into a future month with no
partition will fail.

---

## Operations

- **Firewall:** allow only `22/tcp`, `80/tcp`, `443/tcp`, and `443/udp` (the latter
  for HTTP/3 — a `443/tcp`-only rule silently disables it). The compose intentionally
  does not publish db/redis/backend.
- **Backups:** the data is re-fetchable, but back up `pgdata` anyway (essential once
  user-drawn leases land). Nightly cron with 14-day retention (`crontab -e`; create
  `/var/backups/oyster` first; `%` must be escaped as `\%` in crontab):
  ```cron
  0 3 * * * docker exec oyster_db pg_dump -U oyster_user oyster_health | gzip > /var/backups/oyster/db-$(date +\%F).sql.gz && find /var/backups/oyster -name 'db-*.sql.gz' -mtime +14 -delete
  ```
- **Resources:** the backend image bundles WeasyPrint + Copernicus Marine, so it's
  large and CMEMS/netCDF work is memory-hungry — give the VPS ≥ 4 GB RAM (`worker_geo`
  already restarts each child every 10 tasks to cap memory). If you don't need
  CMEMS, leave its creds blank and those fetches no-op.
- **Logs:** add Docker log rotation (`/etc/docker/daemon.json`:
  `{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}`) so
  worker logs don't fill the disk.
- **CMEMS / PDF:** both work in the backend image (it apt-installs GTK + curl). The
  `/api/areas/{slug}/report.pdf` endpoint renders server-side here (unlike Windows
  host dev, where it 503s and the UI falls back to browser print).

---

## Troubleshooting

- **Cert not issued** — DNS must resolve to the VPS and 80/443 must be open before
  Caddy can complete the ACME challenge. Check `docker logs oyster_web`.
- **502 from `/api`** — backend not healthy; `docker logs oyster_backend`. Usually
  a bad `.env` (e.g. `POSTGRES_HOST` left as `localhost` instead of `db`).
- **Empty map / no verdicts** — the initial seed+compute didn't run; re-run the
  seed/compute block from `deploy.sh`.
- **Migrations** — `docker compose -f app/docker/docker-compose.prod.yml run --rm
  backend python manage.py migrate` is safe to run anytime (idempotent).
