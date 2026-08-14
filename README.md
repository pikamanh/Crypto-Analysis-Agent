# Crypto-Analysis-Agent

A **BTC options analytics dashboard** with an AI agent that interprets the
data: pulls the live BTC options chain from Deribit, computes GEX/DEX,
key levels, and open-interest concentration, then has an LLM agent turn
those numbers into a plain-language market-structure read.

**Live demo**: [crypto-analysis-agent-faxs.onrender.com](https://crypto-analysis-agent-faxs.onrender.com/)

## What it does

1. **Options engine** (`api/options_engine.py`) — fetches the live BTC options
   chain from Deribit's public REST API (no auth needed), computes gamma/delta
   exposure per strike (Black-Scholes, since Deribit doesn't publish greeks
   directly), and derives key levels (call resistance, put support, HVL,
   expected move, IV/HV, gamma regime, OI concentration).
2. **Dashboard** (`api/static/index.html`) — renders that data: GEX by strike,
   open interest by strike, key levels, expiration structure, gamma regime,
   current vs. next week comparison.
3. **Interpretation agent** (`agents/option_agent.py`) — takes the computed
   dashboard data and an LLM (OpenAI-compatible API) produces a structured
   market-structure interpretation (dealer positioning, key levels to watch,
   scenarios). Re-runs only when the underlying data has moved enough to
   change the read (see thresholds in `option_agent.py`), otherwise reuses
   the cached analysis (`agents/.option_cache.json`).

```
Deribit API (public REST)
  └─ options_engine.py → GEX/DEX, key levels, OI concentration
       ├─ /api/options/dashboard        → raw computed data for the dashboard
       └─ agents/option_agent.py (LLM)  → /api/options/interpretation
FastAPI (api/app.py) serves api/static/index.html + the two endpoints above
```

## Tech stack

| Component | Technology |
|---|---|
| Language | Python 3.10+ |
| API/Dashboard | FastAPI + static HTML/JS |
| Options data | Deribit public REST API |
| LLM | OpenAI-compatible API (`agents/option_agent.py`) |
| Deployment | Render (`render.yaml`, Docker, free tier, Singapore region) |

## Project structure

```
api/
  app.py               # FastAPI app — serves dashboard + /api/options/* endpoints
  options_engine.py     # Deribit fetch + GEX/DEX/key-level computation
  static/index.html      # dashboard UI
agents/
  option_agent.py        # LLM interpretation of the computed options data
  system prompt/option.md # system prompt for the interpretation agent
```

## Setup & running

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in OPENAI_API_KEY / BASE_URL for the interpretation agent
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000`.

## Deployment

Deployed on [Render](https://render.com) via `render.yaml` (Docker runtime,
free tier, Singapore region). `API_USERNAME`/`API_PASSWORD` are optional —
without them the app still runs, just without basic auth on the endpoints.

## Note

The repo also contains an earlier, broader "agentic trading bot" effort
(`scheduler/`, `execution/`, `risk/`, `storage/`, `backtest/`, `tools/`,
`GO_LIVE_CHECKLIST.md`, `PLAN.md`, `DEPLOY.md`) that is **not part of the
currently running product** — the deployed app above only serves the options
dashboard and its interpretation agent.
