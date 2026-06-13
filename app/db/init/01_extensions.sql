-- Required PostGIS + utility extensions.
-- Note: pg_partman is NOT in the stock postgis/postgis:16 image. Monthly
-- partitions for data_snapshots are created natively in 03_partitions.sql
-- and extended by app/scripts/create_partitions.py. If we later need
-- pg_partman, build a custom image from postgis/postgis +
-- `apt-get install postgresql-16-partman`.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;       -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS btree_gist;
