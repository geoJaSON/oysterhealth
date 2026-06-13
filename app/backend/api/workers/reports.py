from celery.utils.log import get_task_logger

from celery_app import app

log = get_task_logger(__name__)


@app.task(name="api.workers.reports.generate_pdf_report", bind=True, max_retries=1)
def generate_pdf_report(self, slug: str):
    """Phase 2 async PDF generation via WeasyPrint. Phase 1 uses the sync
    handler in api/routes/areas.py and this task remains unscheduled.
    """
    log.info("TODO: render report HTML, run WeasyPrint, store under reports/%s.pdf", slug)
    return {"status": "stub", "slug": slug}
