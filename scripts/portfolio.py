"""
Portfolio data layer.

Responsible for reading/writing portfolio.json, trades.json and price_history.json.
Also contains pure functions for computing current value, P&L, etc.

Schema:

portfolio.json:
{
  "created_at": "2026-07-27T14:30:00Z",
  "cash_usd": 0.0,
  "holdings": {
    "tendies": {
      "token_id": "tendies",
      "ticker": "TENDIES",
      "name": "TENDIES",
      "chain": "Robinhood",
      "color": "#f59e0b",
      "amount": 83.33,                 # number of token units held
      "cost_basis_usd": 1.00,          # total USD spent on this position
      "last_price": 0.012,             # most recent price we fetched
      "last_price_updated_at": "..."
    },
    ...
  },
  "last_updated_at": "..."
}

trades.json:
[
  { "id": "uuid", "ts": "...", "action": "BUY", "token_id": "tendies",
    "amount": 83.33, "price_usd": 0.012, "value_usd": 1.00, "note": "Initial paper buy" },
  ...
]

price_history.json:
[
  { "ts": "...", "epoch": 1789000000,
    "prices": { "tendies": 0.012, "cashcat": 0.045, "fox": 0.0009, "pump": 0.0018 },
    "portfolio_value_usd": 4.05 },
  ...
]
"""

import uuid
from typing import Optional

from config import TOKENS, INITIAL_BUY_USD, INITIAL_CASH_USD
from utils import (
    read_json, write_json,
    PORTFOLIO_PATH, TRADES_PATH, PRICE_HISTORY_PATH,
    now_iso, now_epoch,
)


def empty_portfolio() -> dict:
    return {
        "created_at": now_iso(),
        "cash_usd": INITIAL_CASH_USD,
        "holdings": {},
        "last_updated_at": now_iso(),
    }


def load_portfolio() -> dict:
    p = read_json(PORTFOLIO_PATH, None)
    if p is None or "holdings" not in p:
        return empty_portfolio()
    return p


def save_portfolio(p: dict) -> None:
    p["last_updated_at"] = now_iso()
    write_json(PORTFOLIO_PATH, p)


def load_trades() -> list:
    return read_json(TRADES_PATH, [])


def save_trades(trades: list) -> None:
    write_json(TRADES_PATH, trades)


def load_price_history() -> list:
    return read_json(PRICE_HISTORY_PATH, [])


def save_price_history(history: list) -> None:
    write_json(PRICE_HISTORY_PATH, history)


def record_trade(action: str, token_id: str, amount: float, price_usd: float,
                 note: str = "", ts: Optional[str] = None) -> dict:
    """
    Record a trade (BUY or SELL) in trades.json and update portfolio holdings.

    action:     'BUY' or 'SELL'
    token_id:   internal token id (must match config.TOKENS)
    amount:     number of token units
    price_usd:  execution price per unit
    """
    assert action in ("BUY", "SELL")
    token = next((t for t in TOKENS if t["id"] == token_id), None)
    if token is None:
        raise ValueError(f"Unknown token_id: {token_id}")
    if amount <= 0:
        raise ValueError("amount must be > 0")
    if price_usd <= 0:
        raise ValueError("price_usd must be > 0")

    ts = ts or now_iso()

    # Load the portfolio and validate BEFORE writing anything, so a rejected
    # trade never lands in trades.json (previously a failed SELL still left a
    # phantom entry in the trade history).
    p = load_portfolio()
    h = p["holdings"].setdefault(token_id, {
        "token_id": token_id,
        "ticker": token["ticker"],
        "name": token["name"],
        "chain": token["chain"],
        "color": token["color"],
        "recommended_by": token.get("recommended_by", "unknown"),
        "amount": 0.0,
        "cost_basis_usd": 0.0,
        "last_price": None,
        "last_price_updated_at": None,
    })

    if action == "SELL":
        held = h["amount"]
        if amount > held:
            # Holdings are stored rounded to 8 decimals, so "sell everything"
            # can exceed the stored balance by a hair. Clamp that to the balance.
            if held > 0 and amount - held <= max(1e-8, held * 1e-9):
                amount = held
            else:
                raise ValueError(f"Insufficient balance: have {held}, want to sell {amount}")

    value_usd = amount * price_usd

    trade = {
        "id": str(uuid.uuid4()),
        "ts": ts,
        "epoch": now_epoch(),
        "action": action,
        "token_id": token_id,
        "ticker": token["ticker"],
        "chain": token["chain"],
        "recommended_by": token.get("recommended_by", "unknown"),
        "amount": amount,
        "price_usd": price_usd,
        "value_usd": round(value_usd, 6),
        "note": note,
    }
    if action == "SELL":
        # Realized P&L of this sale = proceeds minus the share of cost basis sold.
        # Used by scripts/risk.py for the daily loss limit.
        cost_removed = h["cost_basis_usd"] * (amount / h["amount"]) if h["amount"] > 0 else 0.0
        trade["realized_pnl_usd"] = round(value_usd - cost_removed, 6)

    # Append to trades.json
    trades = load_trades()
    trades.append(trade)
    save_trades(trades)

    # Update portfolio
    if action == "BUY":
        if h["amount"] <= 0:
            # Opening a fresh position: rules that already fired on the old
            # (closed) position become eligible again.
            h["position_opened_at"] = ts
            h["entries"] = h.get("entries", 0) + 1
            # A position opened after a stop-loss is the re-entry (see risk.py).
            h["is_reentry"] = h.get("stops_hit", 0) > 0
            if h["is_reentry"]:
                h["reentry_used"] = True
            h.pop("breakeven_alerted", None)
            h.pop("peak_price", None)  # trailing stop restarts from the new entry
        h["amount"] = round(h["amount"] + amount, 8)
        h["cost_basis_usd"] = round(h["cost_basis_usd"] + value_usd, 6)
        p["cash_usd"] = round(p["cash_usd"] - value_usd, 6)
    else:  # SELL (balance already validated above)
        # Reduce cost basis proportionally
        if h["amount"] > 0:
            ratio = amount / h["amount"]
            h["cost_basis_usd"] = round(h["cost_basis_usd"] * (1 - ratio), 6)
        h["amount"] = round(h["amount"] - amount, 8)
        p["cash_usd"] = round(p["cash_usd"] + value_usd, 6)

    save_portfolio(p)
    return trade


def update_prices(prices_by_token: dict) -> None:
    """
    Update each holding's last_price and append a price history snapshot.

    prices_by_token: { token_id: { "price": ..., "change_24h": ..., "market_cap": ..., "volume_24h": ..., "last_updated_at": ... } }
    """
    p = load_portfolio()
    snapshot = {"ts": now_iso(), "epoch": now_epoch(), "prices": {}, "portfolio_value_usd": 0.0}

    portfolio_value = p.get("cash_usd", 0.0)

    for token in TOKENS:
        tid = token["id"]
        price_info = prices_by_token.get(tid, {})
        price = price_info.get("price") if price_info else None

        if tid in p["holdings"]:
            h = p["holdings"][tid]
            if price is not None and price > 0:
                h["last_price"] = price
                h["last_price_updated_at"] = now_iso()
                snapshot["prices"][tid] = price
                portfolio_value += h["amount"] * price
            else:
                # Price couldn't be fetched; keep last known price for portfolio value
                if h["last_price"] is not None:
                    snapshot["prices"][tid] = h["last_price"]
                    portfolio_value += h["amount"] * h["last_price"]

    snapshot["portfolio_value_usd"] = round(portfolio_value, 6)
    save_portfolio(p)

    history = load_price_history()
    history.append(snapshot)
    save_price_history(compact_history(history))


def compact_history(history: list, now_epoch_s: int = None) -> list:
    """Keep the chart useful without letting the file (downloaded by the
    dashboard on every load) grow forever: every snapshot of the last 7 days,
    one per hour up to 90 days, then one per day. Keeps the first snapshot of
    each bucket, so the very first point (initial buy) is never dropped."""
    now_epoch_s = now_epoch_s or now_epoch()
    out, seen = [], set()
    for snap in history:
        age = now_epoch_s - snap.get("epoch", now_epoch_s)
        if age <= 7 * 86400:
            out.append(snap)
            continue
        bucket = ("h", snap["epoch"] // 3600) if age <= 90 * 86400 else ("d", snap["epoch"] // 86400)
        if bucket not in seen:
            seen.add(bucket)
            out.append(snap)
    return out


def compute_summary() -> dict:
    """Return a summary dict: total cost basis, total current value, total P&L,
    per-token info, and per-AI grouped stats."""
    p = load_portfolio()
    total_cost = 0.0
    total_value = p.get("cash_usd", 0.0)
    per_token = []

    for token in TOKENS:
        tid = token["id"]
        h = p["holdings"].get(tid)
        recommended_by = token.get("recommended_by", "unknown")
        if not h:
            per_token.append({
                "token_id": tid, "ticker": token["ticker"], "chain": token["chain"],
                "color": token["color"], "recommended_by": recommended_by,
                "amount": 0, "cost_basis_usd": 0,
                "last_price": None, "current_value_usd": 0, "pnl_usd": 0, "pnl_pct": 0,
            })
            continue
        cost = h["cost_basis_usd"]
        price = h["last_price"] or 0
        value = h["amount"] * price
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0
        total_cost += cost
        total_value += value
        per_token.append({
            "token_id": tid,
            "ticker": h["ticker"],
            "chain": h["chain"],
            "color": h["color"],
            "recommended_by": h.get("recommended_by", recommended_by),
            "amount": h["amount"],
            "cost_basis_usd": cost,
            "last_price": price,
            "current_value_usd": value,
            "pnl_usd": pnl,
            "pnl_pct": pnl_pct,
            "position_opened_at": h.get("position_opened_at"),
        })

    # Money in / money out per token, from the trade history.
    # P&L = value still held + proceeds already cashed out - everything bought.
    # (The old formula, value - open cost basis, ignored realized gains and
    # counted cash as profit.)
    flows = trade_flows()
    for h in per_token:
        f = flows.get(h["token_id"], {"bought": 0.0, "sold": 0.0})
        h["invested_usd"] = f["bought"]
        h["proceeds_usd"] = f["sold"]

    # Per-AI grouping
    per_ai = {}
    for h in per_token:
        ai = h["recommended_by"]
        if ai not in per_ai:
            per_ai[ai] = {
                "ai": ai,
                "num_tokens": 0,
                "cost_basis_usd": 0.0,
                "invested_usd": 0.0,
                "proceeds_usd": 0.0,
                "current_value_usd": 0.0,
                "pnl_usd": 0.0,
                "pnl_pct": 0.0,
                "tokens": [],
            }
        per_ai[ai]["num_tokens"] += 1
        per_ai[ai]["cost_basis_usd"] += h["cost_basis_usd"]
        per_ai[ai]["invested_usd"] += h["invested_usd"]
        per_ai[ai]["proceeds_usd"] += h["proceeds_usd"]
        per_ai[ai]["current_value_usd"] += h["current_value_usd"]
        per_ai[ai]["tokens"].append(h["token_id"])

    for ai, stats in per_ai.items():
        for k in ("cost_basis_usd", "invested_usd", "proceeds_usd", "current_value_usd"):
            stats[k] = round(stats[k], 6)
        stats["pnl_usd"] = round(stats["current_value_usd"] + stats["proceeds_usd"] - stats["invested_usd"], 6)
        stats["pnl_pct"] = (stats["pnl_usd"] / stats["invested_usd"] * 100) if stats["invested_usd"] > 0 else 0

    total_invested = sum(f["bought"] for f in flows.values())
    total_proceeds = sum(f["sold"] for f in flows.values())
    holdings_value = total_value - p.get("cash_usd", 0)
    total_pnl = holdings_value + total_proceeds - total_invested

    return {
        "created_at": p.get("created_at"),
        "last_updated_at": p.get("last_updated_at"),
        "cash_usd": p.get("cash_usd", 0),
        "total_cost_usd": total_cost,              # cost basis of open positions
        "total_invested_usd": total_invested,      # everything ever bought
        "total_proceeds_usd": total_proceeds,      # everything ever sold
        "total_value_usd": total_value,            # cash + open positions
        "total_pnl_usd": total_pnl,
        "total_pnl_pct": (total_pnl / total_invested * 100) if total_invested > 0 else 0,
        "holdings": per_token,
        "per_ai": list(per_ai.values()),
    }


def trade_flows() -> dict:
    """{token_id: {"bought": USD spent, "sold": USD received}} over all trades."""
    flows = {}
    for t in load_trades():
        f = flows.setdefault(t["token_id"], {"bought": 0.0, "sold": 0.0})
        f["bought" if t["action"] == "BUY" else "sold"] += t.get("value_usd", 0)
    return flows
