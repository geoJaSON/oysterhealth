-- 0001 — forecast schema (NWM streamflow forecasts). Added 2026-06.
--
-- Idempotent on purpose: a FRESH database gets these from db/init/02_schema.sql,
-- while an EXISTING database (created before this change) gets them here. Running
-- it twice is a no-op. New schema changes after this go in 0002_*.sql, 0003_*.sql,
-- … and `python manage.py migrate` applies any not-yet-applied file in order.

ALTER TABLE usgs_gauges
  ADD COLUMN IF NOT EXISTS nwm_reach_id TEXT;

CREATE TABLE IF NOT EXISTS nwm_forecasts (
  id         BIGSERIAL PRIMARY KEY,
  gauge_id   UUID NOT NULL REFERENCES usgs_gauges(id) ON DELETE CASCADE,
  reach_id   TEXT NOT NULL,
  series     TEXT NOT NULL CHECK (series IN ('short_range', 'medium_range', 'medium_range_blend')),
  issued_at  TIMESTAMPTZ NOT NULL,
  valid_time TIMESTAMPTZ NOT NULL,
  flow_cfs   NUMERIC,
  CONSTRAINT nwm_forecasts_uniq UNIQUE (gauge_id, series, issued_at, valid_time)
);

CREATE INDEX IF NOT EXISTS idx_nwm_forecasts_lookup
  ON nwm_forecasts (gauge_id, series, valid_time);
