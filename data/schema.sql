-- Raw, immutable, append-only tables. No derived/computed columns here —
-- see feature_gex_snapshot (added later) for anything rebuildable.
-- Requires the timescaledb extension (docker-compose uses the
-- timescale/timescaledb image, which ships it pre-installed).

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS raw_ohlcv (
    ts          TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    open        NUMERIC NOT NULL,
    high        NUMERIC NOT NULL,
    low         NUMERIC NOT NULL,
    close       NUMERIC NOT NULL,
    volume      NUMERIC NOT NULL,
    PRIMARY KEY (ts, symbol, exchange)
);
SELECT create_hypertable('raw_ohlcv', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS raw_futures_snapshot (
    ts              TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    open_interest   NUMERIC NOT NULL,   -- base-asset units (BTC), per exchange API
    funding_rate    NUMERIC NOT NULL,
    mark_price      NUMERIC NOT NULL,
    index_price     NUMERIC NOT NULL,
    PRIMARY KEY (ts, symbol, exchange)
);
SELECT create_hypertable('raw_futures_snapshot', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS raw_options_chain (
    ts                  TIMESTAMPTZ NOT NULL,
    symbol              TEXT NOT NULL,
    exchange            TEXT NOT NULL,
    expiry              DATE NOT NULL,
    strike              NUMERIC NOT NULL,
    option_type         TEXT NOT NULL,  -- 'call' | 'put'
    open_interest       NUMERIC NOT NULL,
    volume              NUMERIC,
    mark_iv             NUMERIC,        -- percent, as published by exchange
    mark_price          NUMERIC,        -- option premium
    underlying_price    NUMERIC NOT NULL,
    PRIMARY KEY (ts, exchange, expiry, strike, option_type)
);
SELECT create_hypertable('raw_options_chain', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS raw_liquidations (
    ts          TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    side        TEXT NOT NULL,  -- 'long' | 'short'
    price       NUMERIC NOT NULL,
    size        NUMERIC NOT NULL
);
SELECT create_hypertable('raw_liquidations', 'ts', if_not_exists => TRUE);
