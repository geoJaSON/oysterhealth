-- ============================================================================
-- Coastal Water Quality Monitor — base schema
-- Mirrors Section 8 of the implementation plan.
-- ============================================================================

-- Named and predefined geographic areas
CREATE TABLE IF NOT EXISTS areas (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  slug          TEXT UNIQUE NOT NULL,
  region        TEXT NOT NULL CHECK (region IN ('gulf', 'east_coast')),
  area_type     TEXT NOT NULL CHECK (area_type IN ('predefined', 'custom')),
  geom          GEOMETRY(POLYGON, 4326) NOT NULL,
  description   TEXT,
  linked_gauges TEXT[],
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_areas_geom   ON areas USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_areas_region ON areas (region);

-- ----------------------------------------------------------------------------
-- data_snapshots is partitioned by month on captured_at.
-- pg_partman maintains future partitions; the script in
-- app/scripts/create_partitions.py kicks it off.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_snapshots (
  id          BIGSERIAL,
  area_id     UUID NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
  captured_at TIMESTAMPTZ NOT NULL,
  variable    TEXT NOT NULL CHECK (variable IN
              ('sst', 'chlorophyll', 'turbidity', 'cdom', 'salinity')),
  value_mean  NUMERIC,
  value_min   NUMERIC,
  value_max   NUMERIC,
  source      TEXT NOT NULL,
  PRIMARY KEY (id, captured_at),
  CONSTRAINT data_snapshots_uniq UNIQUE (area_id, variable, captured_at)
) PARTITION BY RANGE (captured_at);

-- Composite lookup index: area_id filters first, then variable, then time desc
-- for O(1) "latest snapshot" queries on the dashboard. Postgres propagates this
-- to every partition created via the parent.
CREATE INDEX IF NOT EXISTS idx_snapshots_lookup
  ON data_snapshots (area_id, variable, captured_at DESC);

-- NOAA CO-OPS stations
CREATE TABLE IF NOT EXISTS stations (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  station_id TEXT UNIQUE NOT NULL,
  name       TEXT NOT NULL,
  lat        NUMERIC NOT NULL,
  lon        NUMERIC NOT NULL,
  variables  TEXT[]
);
CREATE INDEX IF NOT EXISTS idx_stations_latlon ON stations (lat, lon);

CREATE TABLE IF NOT EXISTS station_readings (
  id          BIGSERIAL PRIMARY KEY,
  station_id  UUID NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
  recorded_at TIMESTAMPTZ NOT NULL,
  variable    TEXT NOT NULL,
  value       NUMERIC NOT NULL,
  unit        TEXT,
  CONSTRAINT station_readings_uniq UNIQUE (station_id, variable, recorded_at)
);
CREATE INDEX IF NOT EXISTS idx_station_readings_lookup
  ON station_readings (station_id, variable, recorded_at DESC);

-- USGS river gauges
CREATE TABLE IF NOT EXISTS usgs_gauges (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_no      TEXT UNIQUE NOT NULL,
  name         TEXT NOT NULL,
  river        TEXT,
  lat          NUMERIC NOT NULL,
  lon          NUMERIC NOT NULL,
  region       TEXT NOT NULL CHECK (region IN ('gulf', 'east_coast')),
  -- NOAA National Water Model reach (NHDPlus COMID) for streamflow FORECASTS,
  -- curated in seed_gauges.py (NLDI coordinate-lookup, magnitude-validated).
  nwm_reach_id TEXT
);

CREATE TABLE IF NOT EXISTS gauge_readings (
  id            BIGSERIAL PRIMARY KEY,
  gauge_id      UUID NOT NULL REFERENCES usgs_gauges(id) ON DELETE CASCADE,
  recorded_at   TIMESTAMPTZ NOT NULL,
  discharge_cfs NUMERIC,
  stage_ft      NUMERIC,
  CONSTRAINT gauge_readings_uniq UNIQUE (gauge_id, recorded_at)
);
CREATE INDEX IF NOT EXISTS idx_gauge_readings_lookup
  ON gauge_readings (gauge_id, recorded_at DESC);

-- NOAA National Water Model streamflow FORECASTS per gauge's NWM reach.
-- One row per (gauge, series, issued_at, valid_time). flow_cfs is the forecast
-- discharge in ft^3/s (NWPS native unit, same as gauge_readings.discharge_cfs).
-- Append-only per issuance; the freshwater_forecast indicator reads the latest
-- issuance's trajectory vs the gauge's recent baseline.
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

-- HAB alerts
CREATE TABLE IF NOT EXISTS hab_alerts (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  region      TEXT NOT NULL,
  alert_level TEXT NOT NULL CHECK (alert_level IN ('watch', 'warning', 'closed')),
  species     TEXT,
  description TEXT,
  issued_at   TIMESTAMPTZ NOT NULL,
  expires_at  TIMESTAMPTZ,
  geom        GEOMETRY(POLYGON, 4326)
);
CREATE INDEX IF NOT EXISTS idx_hab_geom ON hab_alerts USING GIST (geom);

-- GPX tracks (Phase 2 / post-auth)
CREATE TABLE IF NOT EXISTS gpx_tracks (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID,
  filename    TEXT,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gpx_points (
  id          BIGSERIAL PRIMARY KEY,
  track_id    UUID NOT NULL REFERENCES gpx_tracks(id) ON DELETE CASCADE,
  lat         NUMERIC NOT NULL,
  lon         NUMERIC NOT NULL,
  recorded_at TIMESTAMPTZ,
  temperature NUMERIC,
  depth_m     NUMERIC
);

-- Future user-owned areas (Supabase auth required)
CREATE TABLE IF NOT EXISTS user_areas (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL,
  name       TEXT NOT NULL,
  geom       GEOMETRY(POLYGON, 4326) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_areas_user ON user_areas (user_id);
CREATE INDEX IF NOT EXISTS idx_user_areas_geom ON user_areas USING GIST (geom);

-- Computed indicator scores per area per indicator type. Kept as an append-only
-- log; the latest row per (area_id, indicator) is the "current" state.
CREATE TABLE IF NOT EXISTS area_indicators (
  id          BIGSERIAL PRIMARY KEY,
  area_id     UUID NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
  indicator   TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status      TEXT NOT NULL,
  score       NUMERIC,
  components  JSONB,
  CONSTRAINT area_indicators_uniq UNIQUE (area_id, indicator, computed_at)
);
CREATE INDEX IF NOT EXISTS idx_area_indicators_latest
  ON area_indicators (area_id, indicator, computed_at DESC);

-- Celery task error log (referenced in Section 10.3 of the plan)
CREATE TABLE IF NOT EXISTS task_errors (
  id          BIGSERIAL PRIMARY KEY,
  task_name   TEXT NOT NULL,
  task_id     TEXT,
  attempt     INT,
  error_class TEXT,
  message     TEXT,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_errors_recent
  ON task_errors (occurred_at DESC);
