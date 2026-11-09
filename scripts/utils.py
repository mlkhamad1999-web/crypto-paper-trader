"""Small shared helpers."""

import os
import json
import time
from datetime import datetime, timezone

# Resolve paths relative to project root (parent of scripts/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

PORTFOLIO_PATH = os.path.join(DATA_DIR, "portfolio.json")
TRADES_PATH = os.path.join(DATA_DIR, "trades.json")
PRICE_HISTORY_PATH = os.path.join(DATA_DIR, "price_history.json")
CUSTOM_TOKENS_PATH = os.path.join(DATA_DIR, "custom_tokens.json")
RISK_CONFIG_PATH = os.path.join(DATA_DIR, "risk.json")


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 (e.g. '2026-07-27T14:30:00Z')."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_epoch() -> int:
    return int(time.time())


def read_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    # Trailing newline for clean git diffs
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n")


def fmt_usd(x: float, decimals: int = 2) -> str:
    """Format a USD amount with thousands separators."""
    if x is None:
        return "—"
    return f"${x:,.{decimals}f}"


def fmt_pct(x: float) -> str:
    if x is None:
        return "—"
    return f"{x:+.2f}%"
