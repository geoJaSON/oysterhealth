"""Shared helpers for Celery tasks.

`retry_backoff_task` decorator applies the exponential-backoff retry policy
from Section 10.3 of the plan: 60s → 120s → 240s → 480s → 960s, max 5 retries,
with errors logged to the `task_errors` table for monitoring.
"""
import functools
import traceback

import psycopg
from celery.utils.log import get_task_logger

from celery_app import app
from settings import settings

log = get_task_logger(__name__)


def _log_error(task_name: str, task_id: str | None, attempt: int, exc: BaseException) -> None:
    try:
        with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO task_errors (task_name, task_id, attempt, error_class, message)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    task_name,
                    task_id,
                    attempt,
                    type(exc).__name__,
                    f"{exc}\n{traceback.format_exc(limit=5)}",
                ),
            )
    except Exception:  # noqa: BLE001 — never let the error logger crash the task
        log.exception("Failed to record task error")


def retry_backoff_task(name: str, max_retries: int = 5, base_countdown: int = 60):
    """Decorator that registers `func` as a Celery task with exponential backoff.

    Use:
        @retry_backoff_task("api.workers.usgs.fetch_usgs_gauges")
        def fetch_usgs_gauges(self): ...
    """
    def decorator(func):
        @app.task(name=name, bind=True, max_retries=max_retries)
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — fetch errors are intentionally broad
                _log_error(name, self.request.id, self.request.retries, exc)
                countdown = base_countdown * (2 ** self.request.retries)
                raise self.retry(exc=exc, countdown=countdown)

        return wrapper

    return decorator
