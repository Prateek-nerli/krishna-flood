CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE hydro_nodes (
    node_id VARCHAR(50) PRIMARY KEY,
    station_name VARCHAR(255),
    state VARCHAR(50),
    node_type VARCHAR(50),
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    bed_elev_m DOUBLE PRECISION,
    danger_frl_m DOUBLE PRECISION,
    gross_cap_tmc DOUBLE PRECISION,
    geom geometry(Point, 4326)
);

CREATE TABLE river_reaches (
    upstream_node VARCHAR(50) REFERENCES hydro_nodes(node_id),
    downstream_node VARCHAR(50) REFERENCES hydro_nodes(node_id),
    dist_km DOUBLE PRECISION,
    base_lag_hr DOUBLE PRECISION,
    PRIMARY KEY (upstream_node, downstream_node)
);

-- We'll also need timeseries tables for TimescaleDB later, such as river_discharge, etc.
-- Let's create a basic timeseries table for daily discharge as an example for Phase 2.
CREATE TABLE daily_discharge (
    node_id VARCHAR(50) REFERENCES hydro_nodes(node_id),
    time TIMESTAMPTZ NOT NULL,
    discharge_cumecs DOUBLE PRECISION,
    PRIMARY KEY (node_id, time)
);
SELECT create_hypertable('daily_discharge', 'time');
