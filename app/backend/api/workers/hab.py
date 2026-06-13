from celery.utils.log import get_task_logger

from api.workers._base import retry_backoff_task

log = get_task_logger(__name__)


@retry_backoff_task("api.workers.hab.fetch_hab_bulletins")
def fetch_hab_bulletins(self):
    log.info("TODO: poll NOAA National HAB Bulletin RSS + scrape regional pages")
