"""ERDDAP + Copernicus Marine fetches.

ERDDAP variables (SST, chlorophyll, turbidity) call into api/services/erddap.py.
Copernicus Marine salinity is still a Phase 2 stub.
"""
from celery.utils.log import get_task_logger

from api.services import erddap
from api.workers._base import retry_backoff_task

log = get_task_logger(__name__)


def _summary(report: dict) -> str:
    return f"ok={report['ok']} empty={report['empty']} error={report['error']}"


@retry_backoff_task("api.workers.satellite.fetch_sst_all_areas")
def fetch_sst_all_areas(self):
    report = erddap.sync_variable("sst")
    log.info("SST sync: %s", _summary(report))
    return report


@retry_backoff_task("api.workers.satellite.fetch_chlorophyll_all_areas")
def fetch_chlorophyll_all_areas(self):
    report = erddap.sync_variable("chlorophyll")
    log.info("Chlorophyll sync: %s", _summary(report))
    return report


@retry_backoff_task("api.workers.satellite.fetch_turbidity_all_areas")
def fetch_turbidity_all_areas(self):
    report = erddap.sync_variable("turbidity")
    log.info("Turbidity sync: %s", _summary(report))
    return report


@retry_backoff_task("api.workers.satellite.fetch_salinity_all_areas")
def fetch_salinity_all_areas(self):
    log.info("TODO: fetch modeled salinity from Copernicus Marine per area")
