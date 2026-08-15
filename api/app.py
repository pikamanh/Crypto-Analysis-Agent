"""
FastAPI app serving the BTC options dashboard + its LLM interpretation.
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from api.options_engine import get_options_dashboard

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Crypto Options Dashboard API", version="1.0.0")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/options/dashboard", include_in_schema=False)
def options_dashboard() -> dict:
    try:
        return get_options_dashboard()
    except Exception as exc:
        logger.exception("Failed to build BTC options dashboard.")
        raise HTTPException(status_code=502, detail=f"Upstream options data unavailable: {exc}")


@app.get("/api/options/interpretation", include_in_schema=False)
def options_interpretation() -> dict:
    # Lazy import: builds an OpenAI client at import time, which would crash
    # startup if OPENAI_API_KEY isn't set.
    from agents.option_agent import analyze_option_data

    try:
        return {"analysis": analyze_option_data()}
    except Exception as exc:
        logger.exception("Failed to generate market structure interpretation.")
        raise HTTPException(status_code=502, detail=f"Interpretation unavailable: {exc}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.app:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
    )
