from celery.utils.log import get_task_logger

from api.services import nwm as nwm_service
from api.workers._base import retry_backoff_task

log = get_task_logger(__name__)


@retry_backoff_task("api.workers.nwm.fetch_nwm_forecasts", max_retries=2)
def fetch_nwm_forecasts(self):
    """Every 6h: pull NWM short-range + medium-range-blend streamflow forecasts
    for every gauge with an nwm_reach_id, into nwm_forecasts. Feeds the
    freshwater_forecast indicator (the forward-looking pulse signal)."""
    report = nwm_service.sync_forecasts()
    log.info("NWM forecast counts: %s", report)
    return report
