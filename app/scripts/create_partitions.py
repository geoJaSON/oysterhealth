"""Roll the monthly partition window forward for data_snapshots.

03_partitions.sql creates an initial 24-month window when the DB is first
initialized. This script extends that window — call it any time you want to
ensure partitions exist for the next 12 months. Idempotent (CREATE TABLE IF
NOT EXISTS), safe to run repeatedly. Wire it into the weekly maintenance
Celery task once data is flowing.
"""
from _db import conn

EXTEND_MONTHS_AHEAD = 12


def main() -> None:
    sql = f"""
    DO $$
    DECLARE
      m           DATE := date_trunc('month', CURRENT_DATE)::date;
      end_month   DATE := date_trunc('month', CURRENT_DATE)::date
                          + INTERVAL '{EXTEND_MONTHS_AHEAD} months';
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
    with conn() as c:
        c.execute(sql)
        cur = c.execute(
            """
            SELECT count(*) FROM pg_inherits
             WHERE inhparent = 'public.data_snapshots'::regclass
            """
        )
        partition_count = cur.fetchone()[0]
        print(f"data_snapshots now has {partition_count} partitions.")


if __name__ == "__main__":
    main()
