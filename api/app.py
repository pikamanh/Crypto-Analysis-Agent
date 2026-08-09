"""
FastAPI monitoring/control layer for the scheduler loop (Giai đoạn 5).

This app never generates signals or places orders itself — it only reads
state written by `scheduler/loop.py` and toggles the same kill switch file
that loop reads before each cycle. Run alongside the scheduler process, not
instead of it:

    uvicorn api.app:app --host 0.0.0.0 --port 8000
"""
import logging
import os
import secrets
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from execution.execution_engine import get_engine
from scheduler.loop import KILL_SWITCH_PATH, engage_kill_switch, is_kill_switch_active, release_kill_switch
from storage import state

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
API_USERNAME = os.getenv("API_USERNAME")
API_PASSWORD = os.getenv("API_PASSWORD")

if not (API_USERNAME and API_PASSWORD):
    logger.warning(
        "API_USERNAME/API_PASSWORD not set — /status, /positions, /start, /stop are UNAUTHENTICATED. "
        "Set both before exposing this app beyond localhost."
    )

app = FastAPI(title="Crypto Futures Bot Control API", version="1.0.0")
security = HTTPBasic(auto_error=False)


def require_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> None:
    if not (API_USERNAME and API_PASSWORD):
        return  # auth disabled — see startup warning above
    valid = bool(credentials) and secrets.compare_digest(
        credentials.username, API_USERNAME
    ) and secrets.compare_digest(credentials.password, API_PASSWORD)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Basic"})


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


class StatusResponse(BaseModel):
    live_trading: bool
    kill_switch_active: bool
    kill_switch_path: str
    account_equity: Optional[float] = None
    open_position_count: int
    daily_realized_pnl: float
    equity_error: Optional[str] = None


class StopRequest(BaseModel):
    reason: str = "api"


class ActionResponse(BaseModel):
    kill_switch_active: bool
    message: str


@app.get("/status", response_model=StatusResponse, dependencies=[Depends(require_auth)])
def get_status() -> StatusResponse:
    equity, equity_error = None, None
    try:
        equity = get_engine().get_account_equity()
    except Exception as exc:
        equity_error = str(exc)
        logger.exception("Failed to fetch account equity for /status.")

    return StatusResponse(
        live_trading=os.getenv("BINANCE_LIVE_TRADING", "false").lower() == "true",
        kill_switch_active=is_kill_switch_active(),
        kill_switch_path=KILL_SWITCH_PATH,
        account_equity=equity,
        open_position_count=state.get_open_position_count(),
        daily_realized_pnl=state.get_daily_realized_pnl(),
        equity_error=equity_error,
    )


@app.get("/positions", response_model=List[state.Position], dependencies=[Depends(require_auth)])
def get_positions(symbol: Optional[str] = None) -> List[state.Position]:
    return state.get_open_positions(symbol=symbol)


@app.post("/start", response_model=ActionResponse, dependencies=[Depends(require_auth)])
def start_trading() -> ActionResponse:
    release_kill_switch()
    return ActionResponse(kill_switch_active=False, message="Kill switch released — scheduler will trade on its next cycle.")


@app.post("/stop", response_model=ActionResponse, dependencies=[Depends(require_auth)])
def stop_trading(body: StopRequest = StopRequest()) -> ActionResponse:
    engage_kill_switch(body.reason)
    return ActionResponse(kill_switch_active=True, message=f"Kill switch engaged ({body.reason}) — scheduler will skip new orders.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.app:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
    )
