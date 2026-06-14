from celery.utils.log import get_task_logger

from api.services import indicators as ind_service
from api.services import synthesis as synth_service
from api.workers._base import retry_backoff_task

log = get_task_logger(__name__)


@retry_backoff_task("api.workers.scoring.compute_indicator_scores", max_retries=2)
def compute_indicator_scores(self):
    """Daily: recompute every area's indicators AND its briefing, persisting
    both to area_indicators.

    Two steps, in this order (mirrors `manage.py compute-indicators`):
      1. indicators.compute_all() — freshwater_intrusion (+ future indicators).
      2. synthesis.compute_all()  — the oyster_condition briefing, which READS
         the freshwater_intrusion row written in step 1, so it must run after.

    Step 2 used to be missing here: the scheduled job recomputed the freshwater
    indicator but never the briefing the map/UI actually serves, so production
    verdicts froze at whatever the last manual `compute-indicators` produced.
    Both steps now run every cycle.
    """
    indicator_report = ind_service.compute_all()
    log.info("Indicator status counts: %s", indicator_report)
    briefing_report = synth_service.compute_all()
    log.info("Oyster-condition verdict counts: %s", briefing_report)
    return {"indicators": indicator_report, "briefings": briefing_report}
