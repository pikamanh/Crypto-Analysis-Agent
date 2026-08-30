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

-- Not raw: one row per poll instead of one per strike/expiry/type. The full
-- per-instrument chain isn't persisted (nothing reads it back — the live
-- dashboard recomputes straight from Deribit), so only the derived GEX/key
-- levels are kept here to stay within the DB storage cap.
CREATE TABLE IF NOT EXISTS feature_gex_snapshot (
    ts                      TIMESTAMPTZ NOT NULL,
    symbol                  TEXT NOT NULL,
    exchange                TEXT NOT NULL,
    spot_price              NUMERIC NOT NULL,
    call_resistance         NUMERIC,
    put_support             NUMERIC,
    hvl                     NUMERIC,        -- gamma flip level
    day_max                 NUMERIC,
    day_min                 NUMERIC,
    iv                      NUMERIC,        -- implied volatility, 30d, percent
    hv                      NUMERIC,        -- historical volatility, 30d, percent
    iv_rank                 NUMERIC,        -- percent
    -- Top 10 strikes ranked by |net GEX|, most significant first.
    gex_strike_1            NUMERIC,
    gex_net_1               NUMERIC,
    gex_strike_2            NUMERIC,
    gex_net_2               NUMERIC,
    gex_strike_3            NUMERIC,
    gex_net_3               NUMERIC,
    gex_strike_4            NUMERIC,
    gex_net_4               NUMERIC,
    gex_strike_5            NUMERIC,
    gex_net_5               NUMERIC,
    gex_strike_6            NUMERIC,
    gex_net_6               NUMERIC,
    gex_strike_7            NUMERIC,
    gex_net_7               NUMERIC,
    gex_strike_8            NUMERIC,
    gex_net_8               NUMERIC,
    gex_strike_9            NUMERIC,
    gex_net_9               NUMERIC,
    gex_strike_10           NUMERIC,
    gex_net_10              NUMERIC,
    PRIMARY KEY (ts, symbol, exchange)
);
SELECT create_hypertable('feature_gex_snapshot', 'ts', if_not_exists => TRUE);

-- Full per-strike GEX profile (all strikes within ~20% of spot, aggregated
-- across expiries), one row per poll with the profile packed into a JSONB
-- column instead of one row per strike — keeps row count sane (~1440/day at
-- the 60s poll cadence) while still capturing every active strike, not just
-- the top 10. Powers the GEX Interval Map's continuous per-strike history;
-- kept separate from feature_gex_snapshot (top-10 by |GEX|, kept
-- indefinitely for the GEX Level leaderboard) — this table is pruned
-- aggressively since it's the full chain, not just the top 10.
--
-- Retention is NOT add_retention_policy() — that's a background-job policy
-- gated behind the (non-Apache) Timescale license, which managed instances
-- like Aiven's don't ship. Instead data/ingest.py calls drop_chunks()
-- directly on an hourly timer (see prune_options_gex_profile), which is
-- plain Apache-licensed hypertable functionality.
CREATE TABLE IF NOT EXISTS feature_gex_profile_snapshot (
    ts          TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    spot_price  NUMERIC NOT NULL,
    profile     JSONB NOT NULL,  -- [{strike, net_gex, call_gex, put_gex}, ...]
    PRIMARY KEY (ts, symbol, exchange)
);
SELECT create_hypertable('feature_gex_profile_snapshot', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS raw_liquidations (
    ts          TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    side        TEXT NOT NULL,  -- 'long' | 'short'
    price       NUMERIC NOT NULL,
    size        NUMERIC NOT NULL
);
SELECT create_hypertable('raw_liquidations', 'ts', if_not_exists => TRUE);
