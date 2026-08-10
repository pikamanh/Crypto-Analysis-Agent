# Crypto-Analysis-Agent

An agentic AI **crypto futures trading bot**: continuously monitors the market,
generates structured trading signals, passes them through a risk-management layer,
and only then places orders. The project started as a "deep research token" tool
(whitepaper/TVL/market cap lookups); that part is now a secondary chatbot tool —
the current focus is the trading bot.

⚠️ This is a system that can potentially trade real money. Everything defaults to
**testnet/dry-run**; enabling live trading must be an explicit, confirmed action
(see [GO_LIVE_CHECKLIST.md](./GO_LIVE_CHECKLIST.md)), and a kill switch is always
available to stop it immediately.

## Architecture

```
Scheduler (loop every 5 minutes, bounded by Binance API rate limits)
 └─ Analysis Pipeline (LangGraph)
     ├─ Price Action Agent  → 15m OHLC, support/resistance, trend (Binance Futures)
     ├─ Option Flow Agent   → OI by strike, put/call ratio, max pain, GEX/DEX (Deribit)
     ├─ Sentiment Agent     → upcoming/ongoing 3-star economic events (ForexFactory)
     └─ Signal Agent        → merges Price Action + Option Flow (primary) + Sentiment (risk adjustment)
                              → {direction, entry_zone, stop_loss, take_profit, confidence, size_pct, reason}
 └─ Risk Manager     → approve/reject/adjust size based on exposure, max daily loss, min confidence
 └─ Execution Engine → places/closes orders via Binance Futures API (testnet first, separate live flag)
 └─ Position/State Tracker (SQLite) → open positions, order history, PnL
 └─ Monitoring/Alert → structured JSON logs + Telegram for every signal/decision/action

Research/Market Agent (CoinGecko/DeFiLlama) → on-demand lookups, outside the bot's decision loop
Chatbot/API layer → Q&A lookups + bot control (start/stop, view positions/PnL)
```

## Key features

- **Automated analysis every 5 minutes**: combines price action (Binance), option
  flow/GEX-DEX (Deribit), and volatility-risk warnings from the economic calendar
  (ForexFactory) into a structured, machine-actionable trading signal.
- **Risk management**: caps position size (% of capital), max concurrent positions,
  max daily loss, min confidence — blocks/adjusts signals before order entry.
- **Testnet/live execution**: places entry + SL/TP orders automatically via Binance
  USDS-M Futures, with fully separate API keys for testnet and live.
- **Kill switch**: instantly stops new orders via CLI or API; does not auto-release
  on restart.
- **Backtesting**: rule-based replay over historical Binance data (win rate,
  R-multiple, profit factor, max drawdown).
- **Monitoring dashboard + API**: FastAPI (`/status`, `/positions`, `/start`,
  `/stop`) plus a simple web page showing equity/PnL, open positions, and a
  breaker switch.
- **Lookup chatbot**: answers questions about a given coin (price, TVL,
  whitepaper) via CoinGecko/DeFiLlama — not part of the bot's decision loop.

## Tech stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Agent orchestration | LangGraph |
| LLM | OpenAI-compatible API (tool calling) |
| Scheduler | Standalone asyncio loop (`scheduler/loop.py`), 5-minute cycle |
| Execution | `binance-sdk-derivatives-trading-usds-futures` (testnet/live) |
| State/persistence | SQLite (`storage/state.py`) |
| Alerting | JSON log (`alert/alerts.jsonl`) + Telegram bot |
| API/Dashboard | FastAPI + static HTML |
| Secondary data sources | CoinGecko, DeFiLlama, Google Sheets (gspread) |
| Containerization | Docker + docker-compose |

## Data sources

| Data type | Source | Role |
|---|---|---|
| OHLC / candles | Binance Futures API | Primary — price action, support/resistance |
| Option flow, GEX/DEX | Deribit API | Primary — large OI zones, dealer gamma exposure |
| Economic calendar (FOMC/CPI/NFP) | ForexFactory JSON | Secondary — volatility risk warnings |
| Crypto news / sentiment | CryptoPanic/RSS | Secondary, additional reference |
| Price / market cap / TVL / whitepaper | CoinGecko, DeFiLlama | Outside the bot loop — chatbot lookups only |

## Project structure

```
agents/       # research, market, price_action, option_flow, sentiment, signal, orchestrator, chatbot
tools/        # LLM tool-calling wrappers + registry.py
data/         # API clients (coingecko, defillama, binance, deribit, economic_calendar, indicators, sheets)
risk/         # risk_manager.py — size/exposure/daily-loss rules
execution/    # execution_engine.py — places/closes orders via exchange API
storage/      # state.py — SQLite: positions, order history, PnL
scheduler/    # loop.py — analysis → risk → execution loop
backtest/     # engine.py — rule-based replay over historical Binance data
api/          # FastAPI app + static dashboard — bot monitoring/control
alert/        # notifier.py — logging + Telegram alerts
```

## Current status

The project is at **Stage 5/5** per [PLAN.md](./PLAN.md):

- [x] **Stage 1** — Foundation: repo setup, Research Agent (CoinGecko/DeFiLlama)
- [x] **Stage 2** — Market & Price Action Agent, LangGraph Orchestrator
- [x] **Stage 3** — Option Flow Agent (Deribit, GEX/DEX), Sentiment Agent (economic calendar), structured Signal Agent
- [x] **Stage 4** — Risk Manager, Execution Engine (testnet), Scheduler, kill switch, alerting
- [~] **Stage 5** — Backtest engine (done), FastAPI + Dashboard (done), Dockerization (done),
  VPS deployment guide (done, **not yet deployed to an actual VPS**);
  **long-running testnet paper trading has not been run yet** — `scheduler/loop.py`
  needs to run continuously for several weeks before considering go-live

Full plan details and per-item progress are in [PLAN.md](./PLAN.md).

## Setup & running

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API keys (LLM, Binance testnet, etc.)
```

Run the scheduler (testnet, analysis → risk → execution loop):

```bash
python -m scheduler.loop --symbols BTCUSDT,ETHUSDT --interval 300
python -m scheduler.loop --once            # run once for testing
python -m scheduler.loop --kill "reason"   # kill switch: block new orders
python -m scheduler.loop --release         # release the kill switch
```

Run a backtest:

```bash
python -m backtest.engine --symbol BTC --days 90 --out report.json
```

Run the monitoring API/dashboard (`http://localhost:8000`):

```bash
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

Or run everything via Docker:

```bash
docker compose up -d
```

See [DEPLOY.md](./DEPLOY.md) for the detailed VPS deployment guide, and
[GO_LIVE_CHECKLIST.md](./GO_LIVE_CHECKLIST.md) for the mandatory checklist before
enabling live trading.
