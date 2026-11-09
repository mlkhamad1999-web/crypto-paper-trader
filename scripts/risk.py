"""
Risk rules for hand-picked tokens (by default: custom tokens added by contract
address, tagged recommended_by="human").

Config lives in data/risk.json (defaults below). All amounts are USD, because
the paper portfolio is priced in USD.

What is enforced:

  BUY  (trade.py buy, api.py buy) is refused when:
    - today's realized loss (calendar day in `timezone`) has reached
      max_daily_loss_usd -> no new buys until tomorrow
    - the buy would push the open cost basis of all governed positions above
      max_capital_usd
    - the position's cost basis would exceed max_position_usd
      (max_position_exceptional_usd when the buy is flagged --exceptional)
    - the coin already hit its stop and its single re-entry was used

  SELL (manual) is refused on a re-entry position that is still below
    break-even while hold_reentry_until_breakeven is on (override: --force).

  AUTO, on every price refresh (called from rules_engine.evaluate_rules):
    - normal position: sell 100% when P&L <= stop_loss_pct, or when the price
      is trailing_stop_pct below its peak since entry (a losing exit counts as
      a stop hit)
    - re-entry position with hold_reentry_until_breakeven on: no stop at all,
      except reentry_hard_stop_pct if you set one; an alert is logged once the
      position is back at break-even.

Note: prices are checked every ~15 minutes (GitHub Actions cron), so a stop
fills at the price seen at that check, which can be well below the stop level
after a fast dump. That is realistic for memecoins: real stops slip too.
"""

from datetime import datetime, timezone

from utils import read_json, RISK_CONFIG_PATH
from config import TOKENS
from portfolio import load_portfolio, save_portfolio, load_trades, compute_summary, record_trade
from activity_log import log_event


DEFAULT_RISK_CONFIG = {
    "enabled": True,
    "applies_to": ["human"],               # recommended_by values governed by these rules
    "max_capital_usd": 50.0,               # max open cost basis across governed positions
    "max_daily_loss_usd": 10.0,            # realized loss per calendar day -> buys blocked
    "max_position_usd": 5.0,               # standard max size of one position
    "max_position_exceptional_usd": 8.0,   # max size with --exceptional
    "stop_loss_pct": -15.0,
    "trailing_stop_pct": None,             # e.g. 30: sell if price falls 30% from its peak
    "allow_one_reentry_after_stop": True,
    "hold_reentry_until_breakeven": True,  # re-entry has no stop and can't be sold at a loss
    "reentry_hard_stop_pct": None,         # e.g. -50 to cap the re-entry's loss; None = no cap
    "safety_recheck_hours": 6,             # re-run the anti-scam check on open positions; None = off
    "exit_on_new_red_flag": False,         # True = also sell when a new major red flag appears
    "timezone": "Europe/Brussels",
}


class RiskViolation(Exception):
    """Raised when an action breaks a risk rule."""


def load_risk_config() -> dict:
    cfg = dict(DEFAULT_RISK_CONFIG)
    user = read_json(RISK_CONFIG_PATH, None)
    if isinstance(user, dict):
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    return cfg


def _token(token_id: str):
    return next((t for t in TOKENS if t["id"] == token_id), None)


def is_governed(token_id: str, cfg: dict = None) -> bool:
    cfg = cfg or load_risk_config()
    t = _token(token_id)
    return bool(cfg.get("enabled")) and t is not None and t.get("recommended_by") in cfg.get("applies_to", [])


def _tz(cfg: dict):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(cfg.get("timezone") or "UTC")
    except Exception:
        print(f"[warn] timezone {cfg.get('timezone')!r} unavailable, using UTC for the daily loss limit")
        return timezone.utc


def realized_pnl_today(cfg: dict = None) -> float:
    """Net realized P&L (USD) of governed tokens for today's calendar day."""
    cfg = cfg or load_risk_config()
    tz = _tz(cfg)
    today = datetime.now(tz).date()
    total = 0.0
    for t in load_trades():
        if t.get("action") != "SELL" or "realized_pnl_usd" not in t:
            continue
        if not is_governed(t["token_id"], cfg):
            continue
        ts = datetime.strptime(t["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if ts.astimezone(tz).date() == today:
            total += t["realized_pnl_usd"]
    return total


def deployed_capital(cfg: dict = None) -> float:
    """Open cost basis (USD) across governed positions."""
    cfg = cfg or load_risk_config()
    p = load_portfolio()
    return sum(h.get("cost_basis_usd", 0) for tid, h in p["holdings"].items()
               if h.get("amount", 0) > 0 and is_governed(tid, cfg))


def check_buy(token_id: str, usd: float, exceptional: bool = False) -> list:
    """Raise RiskViolation if this buy breaks a rule. Returns a list of warnings."""
    cfg = load_risk_config()
    if not is_governed(token_id, cfg):
        return []
    if usd <= 0:
        raise RiskViolation("Buy amount must be > 0")
    warnings = []

    loss_limit = float(cfg["max_daily_loss_usd"])
    pnl_today = realized_pnl_today(cfg)
    if pnl_today <= -loss_limit:
        raise RiskViolation(
            f"Daily loss limit reached: {pnl_today:+.2f} USD realized today (limit -{loss_limit:.2f}). "
            f"No new buys until tomorrow ({cfg['timezone']}).")

    p = load_portfolio()
    h = p["holdings"].get(token_id, {})
    opening = h.get("amount", 0) <= 0

    if opening and h.get("stops_hit", 0) > 0:
        if not cfg.get("allow_one_reentry_after_stop"):
            raise RiskViolation("This coin already hit its stop and re-entries are disabled.")
        if h.get("reentry_used"):
            raise RiskViolation("This coin already hit its stop and its single re-entry was used. No more entries.")
        if cfg.get("hold_reentry_until_breakeven"):
            hard = cfg.get("reentry_hard_stop_pct")
            warnings.append(
                "This is your ONE re-entry on this coin. It will have NO stop-loss"
                + (f" except a hard stop at {hard}%" if hard is not None else "")
                + " and cannot be sold below break-even.")
        else:
            warnings.append("This is your ONE re-entry on this coin.")

    position_cost = (0.0 if opening else h.get("cost_basis_usd", 0)) + usd
    limit = float(cfg["max_position_exceptional_usd"] if exceptional else cfg["max_position_usd"])
    if position_cost > limit + 1e-9:
        hint = "" if exceptional else f" (up to {cfg['max_position_exceptional_usd']} with --exceptional)"
        raise RiskViolation(f"Position would be {position_cost:.2f} USD, above the {limit:.2f} USD max{hint}.")

    deployed = deployed_capital(cfg)
    cap = float(cfg["max_capital_usd"])
    if deployed + usd > cap + 1e-9:
        raise RiskViolation(f"Capital cap: {deployed:.2f} USD already deployed + {usd:.2f} > {cap:.2f} USD max.")

    return warnings


def check_sell(token_id: str, price: float) -> None:
    """Raise RiskViolation if selling now breaks the hold-until-break-even rule."""
    cfg = load_risk_config()
    if not is_governed(token_id, cfg) or not cfg.get("hold_reentry_until_breakeven"):
        return
    h = load_portfolio()["holdings"].get(token_id)
    if not h or not h.get("is_reentry") or h.get("amount", 0) <= 0:
        return
    value = h["amount"] * price
    if value < h["cost_basis_usd"]:
        raise RiskViolation(
            f"Re-entry position is below break-even (value {value:.2f} vs cost {h['cost_basis_usd']:.2f} USD). "
            "Your rule says hold until break-even. Use --force to override.")


def evaluate_risk_stops() -> list:
    """Run the automatic stop-loss / re-entry logic. Returns triggered events
    in the same shape as rules_engine.evaluate_rules."""
    cfg = load_risk_config()
    if not cfg.get("enabled"):
        return []
    triggered = []

    for h in compute_summary()["holdings"]:
        tid = h["token_id"]
        if not is_governed(tid, cfg) or h["amount"] <= 0 or h["cost_basis_usd"] <= 0 or not h["last_price"]:
            continue
        p = load_portfolio()
        ph = p["holdings"][tid]
        pnl_pct = h["pnl_pct"]

        if ph.get("is_reentry") and cfg.get("hold_reentry_until_breakeven"):
            hard = cfg.get("reentry_hard_stop_pct")
            if hard is not None and pnl_pct <= float(hard):
                rule_id, threshold = "risk-reentry-hard-stop", float(hard)
            else:
                if pnl_pct >= 0 and not ph.get("breakeven_alerted"):
                    ph["breakeven_alerted"] = True
                    save_portfolio(p)
                    event = {"rule_id": "risk-reentry-breakeven", "rule_type": "alert", "token_id": tid,
                             "ticker": h["ticker"], "pnl_pct_at_trigger": pnl_pct, "threshold": 0,
                             "action": "alert_only"}
                    log_event("risk", "rule_triggered",
                              f"{h['ticker']} re-entry is back at break-even ({pnl_pct:+.2f}%): your rules now allow an exit",
                              event)
                    triggered.append(event)
                continue
        else:
            threshold = float(cfg["stop_loss_pct"])
            price = h["last_price"]
            # Trailing stop: highest price seen since the position opened.
            # (Checked every ~15 min, so the peak is the best observed price.)
            peak = max(ph.get("peak_price") or 0, price)
            if peak != ph.get("peak_price"):
                ph["peak_price"] = peak
                save_portfolio(p)
            trail = cfg.get("trailing_stop_pct")
            trail_hit = bool(trail) and price <= peak * (1 - float(trail) / 100)
            if pnl_pct <= threshold:
                rule_id = "risk-stop-loss"
            elif trail_hit:
                rule_id, threshold = "risk-trailing-stop", -float(trail)
            else:
                continue

        try:
            trade = record_trade("SELL", tid, h["amount"], h["last_price"],
                                 note=f"Auto: {rule_id} triggered (@ {pnl_pct:+.2f}% vs entry, level {threshold}%)")
        except Exception as e:
            log_event("system", "rule_error", f"Failed to execute {rule_id} on {h['ticker']}: {e}",
                      {"rule_id": rule_id, "error": str(e)})
            continue

        # A losing exit counts as a stop hit (it unlocks the single re-entry);
        # a trailing stop that locks in a profit does not.
        if (trade.get("realized_pnl_usd") or 0) < 0:
            p = load_portfolio()
            ph = p["holdings"][tid]
            ph["stops_hit"] = ph.get("stops_hit", 0) + 1
            save_portfolio(p)

        event = {"rule_id": rule_id, "rule_type": "stop_loss", "token_id": tid, "ticker": h["ticker"],
                 "pnl_pct_at_trigger": pnl_pct, "threshold": threshold, "action": "sell_100pct",
                 "trade_id": trade["id"], "amount_sold": trade["amount"], "price_usd": trade["price_usd"],
                 "realized_pnl_usd": trade.get("realized_pnl_usd")}
        log_event("risk", "rule_triggered",
                  f"{rule_id} on {h['ticker']} at {pnl_pct:+.2f}% → sold 100% "
                  f"(realized {trade.get('realized_pnl_usd', 0):+.2f} USD)", event)
        triggered.append(event)

    return triggered


def _flag_kind(label: str) -> str:
    """'Top 10 holders 66.0%' -> 'Top 10 holders #%' so a changing number is
    not reported as a new flag every time."""
    import re
    return re.sub(r"\d+(?:[.,]\d+)*", "#", label)


def recheck_open_positions() -> list:
    """Re-run the anti-scam check on open hand-picked positions every
    `safety_recheck_hours`. Logs a safety_alert when a NEW major red flag shows
    up (dev dump, liquidity pulled, concentration jump...). Sells the position
    too if `exit_on_new_red_flag` is true. Called from update_portfolio.py."""
    cfg = load_risk_config()
    hours = cfg.get("safety_recheck_hours")
    if not cfg.get("enabled") or not hours:
        return []
    import token_check
    from utils import now_iso

    now = datetime.now(timezone.utc)
    alerts = []
    for tid, h in list(load_portfolio()["holdings"].items()):
        tok = _token(tid)
        if h.get("amount", 0) <= 0 or not is_governed(tid, cfg) or not tok:
            continue
        if tok.get("chain") != "Solana" or not tok.get("contract"):
            continue
        last = h.get("safety_checked_at")
        if last and (now - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() < hours * 3600:
            continue
        try:
            r = token_check.check(tok["contract"])
        except Exception as e:
            print(f"[warn] safety re-check failed for {tok['ticker']}: {e}")
            continue
        reds = sorted({_flag_kind(label) for level, label, _ in r["flags"] if level == "RED"})
        details = [label for level, label, _ in r["flags"] if level == "RED"]
        p = load_portfolio()
        ph = p["holdings"][tid]
        new = [k for k in reds if k not in set(ph.get("safety_reds") or [])]
        ph["safety_checked_at"] = now_iso()
        ph["safety_reds"] = reds
        save_portfolio(p)
        if not new:
            continue
        event = {"token_id": tid, "ticker": tok["ticker"], "new_red_flags": details, "red": r["red"], "warn": r["warn"]}
        log_event("risk", "safety_alert", f"{tok['ticker']}: new red flag(s) on an open position: {'; '.join(details)}", event)
        alerts.append(event)
        if cfg.get("exit_on_new_red_flag") and ph.get("last_price"):
            try:
                trade = record_trade("SELL", tid, ph["amount"], ph["last_price"],
                                     note=f"Auto: risk-safety-exit (new red flag: {'; '.join(details)})")
                log_event("risk", "rule_triggered", f"risk-safety-exit on {tok['ticker']} → sold 100% "
                          f"(realized {trade.get('realized_pnl_usd', 0):+.2f} USD)", {"trade_id": trade["id"], **event})
                event["sold"] = True
            except Exception as e:
                log_event("system", "rule_error", f"Failed safety exit on {tok['ticker']}: {e}", {"error": str(e)})
    return alerts


def risk_status() -> dict:
    """Snapshot of the risk state, for `trade.py risk`."""
    cfg = load_risk_config()
    p = load_portfolio()
    positions = []
    for t in TOKENS:
        if not is_governed(t["id"], cfg):
            continue
        h = p["holdings"].get(t["id"], {})
        positions.append({
            "token_id": t["id"], "ticker": t["ticker"], "contract": t.get("contract"),
            "amount": h.get("amount", 0), "cost_basis_usd": h.get("cost_basis_usd", 0),
            "last_price": h.get("last_price"), "stops_hit": h.get("stops_hit", 0),
            "is_reentry": h.get("is_reentry", False), "reentry_used": h.get("reentry_used", False),
        })
    return {
        "config": cfg,
        "realized_pnl_today_usd": realized_pnl_today(cfg),
        "deployed_usd": deployed_capital(cfg),
        "positions": positions,
    }
