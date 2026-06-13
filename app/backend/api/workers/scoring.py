from celery.utils.log import get_task_logger

from api.services import indicators as ind_service
from api.workers._base import retry_backoff_task

log = get_task_logger(__name__)


@retry_backoff_task("api.workers.scoring.compute_indicator_scores", max_retries=2)
def compute_indicator_scores(self):
    """Daily: recompute the freshwater-intrusion (and later oyster-drill)
    indicator for every area, persist to area_indicators.
    """
    report = ind_service.compute_all()
    log.info("Indicator status counts: %s", report)
    return report
