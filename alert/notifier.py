import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

ALERT_LOG_PATH = os.getenv("ALERT_LOG_PATH", os.path.join(os.path.dirname(__file__), "alerts.jsonl"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception:
        logger.exception("Failed to send Telegram alert.")


def notify(event_type: str, payload: dict[str, Any], message: Optional[str] = None) -> None:
    """
    Record a structured event (signal / risk decision / execution action / kill switch)
    as a JSON line, and best-effort push it to Telegram if configured. Never raises —
    an alerting failure must not interrupt the trading loop.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    try:
        os.makedirs(os.path.dirname(ALERT_LOG_PATH) or ".", exist_ok=True)
        with open(ALERT_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        logger.exception("Failed to write alert log.")

    logger.info(f"[{event_type}] {message or json.dumps(payload)}")
    _send_telegram(message or f"[{event_type}] {json.dumps(payload)}")


def notify_signal(symbol: str, signal: Optional[Any]) -> None:
    payload = signal.model_dump() if signal else None
    direction = payload["direction"] if payload else "none"
    notify(
        "signal",
        {"symbol": symbol, "signal": payload},
        message=f"Signal {symbol}: {direction} (confidence={payload.get('confidence') if payload else 'n/a'})",
    )


def notify_risk_decision(decision: Any) -> None:
    notify(
        "risk_decision",
        decision.model_dump(),
        message=f"Risk {decision.action} {decision.symbol} {decision.direction}: {decision.reason}",
    )


def notify_execution(symbol: str, action: str, details: dict[str, Any]) -> None:
    notify(
        "execution",
        {"symbol": symbol, "action": action, "details": details},
        message=f"Execution {action} {symbol}: {details}",
    )


def notify_kill_switch(active: bool, reason: str = "") -> None:
    notify(
        "kill_switch",
        {"active": active, "reason": reason},
        message=f"Kill switch {'ENGAGED' if active else 'RELEASED'}: {reason}",
    )
