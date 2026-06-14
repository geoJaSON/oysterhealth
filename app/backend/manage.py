"""One-off task runner: data fetches, backfills, partition maintenance.

Each subcommand calls the same function the corresponding Celery task does,
so behavior matches what beat would have triggered. Useful on Windows where
running a long-lived Celery worker is fragile.

Usage (from app/backend with the venv active):
    python manage.py fetch-usgs
    python manage.py backfill-usgs              # default 90 days
    python manage.py backfill-usgs 30           # 30 days
    python manage.py extend-partitions
"""
from __future__ import annotations

import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )


def cmd_fetch_usgs() -> None:
    from api.services import usgs
    n = usgs.sync_latest()
    print(f"Upserted {n} latest USGS readings.")


def cmd_backfill_usgs(days: int = 90) -> None:
    from api.services import usgs
    n = usgs.sync_historical_daily(days_back=int(days))
    print(f"Upserted {n} historical USGS readings ({days} days).")


def cmd_fetch_erddap(variable: str | None = None) -> None:
    """Fetch the latest ERDDAP grid for one variable, or all if omitted."""
    from api.services import erddap
    if variable is None:
        reports = erddap.sync_all()
        for var, r in reports.items():
            print(f"{var}: ok={r['ok']} empty={r['empty']} error={r['error']} rows={r.get('rows', r['ok'])}")
    else:
        r = erddap.sync_variable(variable)
        print(f"{variable}: ok={r['ok']} empty={r['empty']} error={r['error']} rows={r['rows']}")
        for slug, msg in r["areas"].items():
            print(f"  {slug}: {msg}")


def cmd_backfill_erddap(variable: str, days: int = 30) -> None:
    """Backfill the last `days` of `variable` from ERDDAP into data_snapshots."""
    from api.services import erddap
    r = erddap.sync_variable_range(variable, int(days))
    print(f"{variable} backfill ({days}d): ok={r['ok']} empty={r['empty']} error={r['error']} rows={r['rows']}")


def cmd_fetch_coops() -> None:
    from api.services import coops
    r = coops.sync_latest()
    print(f"CO-OPS latest: requests={r['requests']} rows={r['rows']} "
          f"stations_with_data={r['stations_with_data']} errors={r['errors']}")


def cmd_backfill_coops(days: int = 30) -> None:
    from api.services import coops
    r = coops.sync_historical(days_back=int(days))
    print(f"CO-OPS backfill ({days}d): requests={r['requests']} rows={r['rows']} errors={r['errors']}")


def cmd_fetch_cmems(days: int = 1) -> None:
    from api.services import cmems
    r = cmems.sync_salinity(days_back=int(days))
    print(f"CMEMS salinity: ok={r['ok']} empty={r['empty']} rows={r['rows']} areas={r['areas']}")


def cmd_fetch_nwm() -> None:
    """Fetch NWM short-range + medium-range-blend streamflow forecasts for every
    gauge with an nwm_reach_id, into nwm_forecasts (feeds the freshwater forecast)."""
    from api.services import nwm
    r = nwm.sync_forecasts()
    print(f"NWM forecasts: gauges={r['gauges']} points={r['points']} errors={r['errors']}")


def cmd_compute_indicators() -> None:
    from api.services import indicators, synthesis
    r = indicators.compute_all()
    print("Freshwater-intrusion status counts:")
    for status, n in sorted(r.items()):
        print(f"  {status}: {n}")
    # Briefings depend on freshwater_intrusion being current, so run them after.
    b = synthesis.compute_all()
    print("Oyster-condition verdict counts:")
    for status, n in sorted(b.items()):
        print(f"  {status}: {n}")


def cmd_extend_partitions() -> None:
    import psycopg
    from settings import settings
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
    print("Extended data_snapshots partition window 12 months ahead.")


COMMANDS = {
    "fetch-usgs":        cmd_fetch_usgs,
    "backfill-usgs":     cmd_backfill_usgs,
    "fetch-erddap":      cmd_fetch_erddap,
    "backfill-erddap":   cmd_backfill_erddap,
    "fetch-coops":       cmd_fetch_coops,
    "backfill-coops":    cmd_backfill_coops,
    "fetch-cmems":       cmd_fetch_cmems,
    "fetch-nwm":         cmd_fetch_nwm,
    "compute-indicators": cmd_compute_indicators,
    "extend-partitions": cmd_extend_partitions,
}


def main() -> None:
    _setup_logging()
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        names = " | ".join(COMMANDS)
        print(f"Usage: python manage.py <{names}> [args...]")
        sys.exit(1)
    name, *args = sys.argv[1:]
    COMMANDS[name](*args)


if __name__ == "__main__":
    main()
