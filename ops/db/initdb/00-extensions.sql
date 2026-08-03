-- Runs once on first volume creation (S0.2 §6). Migrations own everything else
-- from Sprint S1 onward. TimescaleDB is a PG16 extension (TDR §8).
CREATE EXTENSION IF NOT EXISTS timescaledb;
