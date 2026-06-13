-- ============================================================================
-- Native monthly partitions for data_snapshots.
-- Creates partitions covering (today - 6 months) through (today + 18 months)
-- so backfilled historical fetches and future writes both have a home.
--
-- Run app/scripts/create_partitions.py periodically (or wire it into the
-- weekly maintenance Celery task) to roll the window forward.
-- ============================================================================
DO $$
DECLARE
  start_month DATE := date_trunc('month', CURRENT_DATE)::date - INTERVAL '6 months';
  end_month   DATE := date_trunc('month', CURRENT_DATE)::date + INTERVAL '18 months';
  m           DATE := start_month;
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
