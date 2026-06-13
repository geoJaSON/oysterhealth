from celery.utils.log import get_task_logger

from api.services import usgs as usgs_service
from api.workers._base import retry_backoff_task

log = get_task_logger(__name__)


@retry_backoff_task("api.workers.usgs.fetch_usgs_gauges")
def fetch_usgs_gauges(self):
    """Hourly: pull the latest instantaneous values for every seeded gauge."""
    n = usgs_service.sync_latest(period="PT2H")
    log.info("Upserted %d gauge readings", n)
    return n
