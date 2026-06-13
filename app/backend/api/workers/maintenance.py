from celery.utils.log import get_task_logger

import psycopg

from api.workers._base import retry_backoff_task
from settings import settings

log = get_task_logger(__name__)


@retry_backoff_task("api.workers.maintenance.cleanup_old_snapshots", max_retries=1)
def cleanup_old_snapshots(self):
    """Extend the monthly partition window 12 months ahead.

    Native partitioning (no pg_partman). Detaching/archiving partitions older
    than 24 months is a separate, manual operation for now — add when data
    volume actually warrants it.
    """
    sql = """
    DO $$
    DECLARE
      m           DATE := date_trunc('month', CURRENT_DATE)::date;
      end_month   DATE := date_trunc('month', CURRENT_DATE)::date + INTERVAL '12 months';
      partition_name TEXT;
    BEGIN
      WHILE m < end_month LOOP
        partition_name := format('data_snapshots_%s', to_char(m, 'YYYY_MM'));
        EXECUTE format(
          'CREATE TABLE IF NOT EXISTS %I PARTITION OF data_snapshots
             FOR VALUES FROM (%L) TO (%L)',
          partition_name, m, m + INTERVAL '1 month'
        );
        m := m + INTERVAL '1 month';
      END LOOP;
    END $$;
    """
    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        conn.execute(sql)
    log.info("Extended data_snapshots partition window 12 months ahead")
