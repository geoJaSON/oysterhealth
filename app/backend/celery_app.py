"""Celery app — split io_bound from geo_heavy so CMEMS netCDF downloads
can't starve the hourly USGS / CO-OPS fetches users depend on.
"""
from celery import Celery
from celery.schedules import crontab

from settings import settings

app = Celery(
    "oyster_health",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "api.workers.satellite",
        "api.workers.usgs",
        "api.workers.noaa",
        "api.workers.hab",
        "api.workers.scoring",
        "api.workers.reports",
        "api.workers.maintenance",
    ],
)

# --- Queue routing ---
app.conf.task_routes = {
    "api.workers.satellite.*":   {"queue": "geo_heavy"},
    "api.workers.scoring.*":     {"queue": "geo_heavy"},
    "api.workers.reports.*":     {"queue": "geo_heavy"},
    "api.workers.maintenance.*": {"queue": "geo_heavy"},
    "api.workers.usgs.*":        {"queue": "io_bound"},
    "api.workers.noaa.*":        {"queue": "io_bound"},
    "api.workers.hab.*":         {"queue": "io_bound"},
}

# --- Memory hygiene ---
# CMEMS / copernicusmarine accumulates memory across runs. Restart each child
# worker after 10 tasks to fully mitigate.
app.conf.worker_max_tasks_per_child = 10

app.conf.task_acks_late = True
app.conf.task_reject_on_worker_lost = True
app.conf.timezone = "UTC"

# --- Beat schedule ---
# Mirrors Section 10.1 of the plan. Tasks themselves are stubs to be filled
# in during Phase 1/2.
app.conf.beat_schedule = {
    "fetch-sst-every-6h": {
        "task": "api.workers.satellite.fetch_sst_all_areas",
        "schedule": crontab(minute=15, hour="*/6"),
    },
    "fetch-chlorophyll-every-6h": {
        "task": "api.workers.satellite.fetch_chlorophyll_all_areas",
        "schedule": crontab(minute=25, hour="*/6"),
    },
    "fetch-turbidity-daily": {
        "task": "api.workers.satellite.fetch_turbidity_all_areas",
        "schedule": crontab(minute=0, hour=2),
    },
    "fetch-salinity-daily": {
        "task": "api.workers.satellite.fetch_salinity_all_areas",
        "schedule": crontab(minute=0, hour=3),
    },
    "fetch-usgs-hourly": {
        "task": "api.workers.usgs.fetch_usgs_gauges",
        "schedule": crontab(minute=5, hour="*"),
    },
    "fetch-noaa-hourly": {
        "task": "api.workers.noaa.fetch_noaa_stations",
        "schedule": crontab(minute=10, hour="*"),
    },
    "fetch-hab-daily": {
        "task": "api.workers.hab.fetch_hab_bulletins",
        "schedule": crontab(minute=0, hour=8),
    },
    "compute-indicators-daily": {
        "task": "api.workers.scoring.compute_indicator_scores",
        "schedule": crontab(minute=0, hour=6),
    },
    "cleanup-weekly": {
        "task": "api.workers.maintenance.cleanup_old_snapshots",
        "schedule": crontab(minute=0, hour=1, day_of_week=0),
    },
}
