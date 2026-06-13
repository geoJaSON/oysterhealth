from celery.utils.log import get_task_logger

from api.services import coops as coops_service
from api.workers._base import retry_backoff_task

log = get_task_logger(__name__)


@retry_backoff_task("api.workers.noaa.fetch_noaa_stations")
def fetch_noaa_stations(self):
    """Hourly: pull the latest CO-OPS reading for every supported
    (station, variable) pair into station_readings.
    """
    report = coops_service.sync_latest()
    log.info(
        "CO-OPS sync: %d requests, %d rows, %d errors",
        report["requests"], report["rows"], report["errors"],
    )
    return report
