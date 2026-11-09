"""
Automated trading rules engine.

Rules are defined in data/rules.json. Each rule has:
  - id:           unique identifier
  - enabled:      bool
  - token_id:     which token to apply to (or "all": all of the creator AI's own
                  picks, or every token if created_by is "human"/"system")
  - type:         "take_profit" | "stop_loss" | "trailing_stop" | "rebalance"
  - threshold:    the trigger value (e.g. 50 for 50% gain on take_profit)
  - action:       what to do when triggered (e.g. "sell_50pct")
  - created_by:   who created the rule (zAI, gAi, human, system)
  - note:         human-readable description

The engine runs after every price update and checks all enabled rules.
When a rule triggers, it executes the action (paper trade) and logs it.

Example rules.json:
[
  {
    "id": "tp-tendies-2x",
    "enabled": true,
    "token_id": "tendies",
    "type": "take_profit",
    "threshold": 100,           // 100% gain = 2x
    "action": "sell_50pct",
    "created_by": "zAI",
    "note": "Sell half of TENDIES if it 2x's"
  },
  {
    "id": "sl-fox-50pct",
    "enabled": true,
    "token_id": "fox",
    "type": "stop_loss",
    "threshold": -50,           // 50% loss
    "action": "sell_100pct",
    "created_by": "zAI",
    "note": "Cut FOX losses at -50%"
  }
]

Supported actions:
  - "sell_25pct", "sell_50pct", "sell_75pct", "sell_100pct"
  - "sell_50usd" (sell $50 worth)
  - "alert_only" (just log, don't sell)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import read_json, write_json, now_iso
from portfolio import load_portfolio, record_trade, compute_summary
from fetch_prices import fetch_all_token_prices
from activity_log import log_event
from risk import evaluate_risk_stops

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "rules.json")

DEFAULT_RULES = [
    {
        "id": "tp-zai-basket-2x",
        "enabled": True,
        "token_id": "all",
        "type": "take_profit",
        "threshold": 100,
        "action": "sell_50pct",
        "created_by": "zAI",
        "note": "Take 50% profit on any zAI pick that 2x's"
    },
    {
        "id": "sl-zai-basket-80pct",
        "enabled": True,
        "token_id": "all",
        "type": "stop_loss",
        "threshold": -80,
        "action": "sell_100pct",
        "created_by": "zAI",
        "note": "Cut losses at -80% (near-zero)"
    },
    {
        "id": "tp-gai-basket-2x",
        "enabled": True,
        "token_id": "all",
        "type": "take_profit",
        "threshold": 100,
        "action": "sell_50pct",
        "created_by": "gAi",
        "note": "Take 50% profit on any gAi pick that 2x's"
    },
]


def load_rules() -> list:
    rules = read_json(RULES_PATH, None)
    if rules is None:
        # First run — create default rules
        write_json(RULES_PATH, DEFAULT_RULES)
        return list(DEFAULT_RULES)
    return rules


def save_rules(rules: list) -> None:
    write_json(RULES_PATH, rules)


def add_rule(rule: dict) -> dict:
    """Add a new rule. Returns the added rule."""
    rules = load_rules()
    if "id" not in rule:
        import uuid
        rule["id"] = f"rule-{str(uuid.uuid4())[:8]}"
    rule.setdefault("enabled", True)
    rule.setdefault("created_by", "system")
    rule.setdefault("note", "")
    rules.append(rule)
    save_rules(rules)
    log_event(rule.get("created_by", "system"), "rule_added", f"Added rule {rule['id']}: {rule.get('note', '')}", rule)
    return rule


def remove_rule(rule_id: str) -> bool:
    rules = load_rules()
    new_rules = [r for r in rules if r.get("id") != rule_id]
    if len(new_rules) == len(rules):
        return False
    save_rules(new_rules)
    log_event("system", "rule_removed", f"Removed rule {rule_id}")
    return True


def _pct_to_sell(action: str) -> float:
    """Parse a sell action string and return the percentage (0-100) to sell."""
    if action == "sell_25pct": return 25.0
    if action == "sell_50pct": return 50.0
    if action == "sell_75pct": return 75.0
    if action == "sell_100pct": return 100.0
    if action == "alert_only": return 0.0
    return 0.0


# Rules created by these actors with token_id "all" apply to every holding.
# Rules created by an AI (zAI, gAi, ...) with token_id "all" apply only to that
# AI's own picks, so one AI's rules never trade another AI's basket.
GLOBAL_RULE_ACTORS = {"human", "system"}


def _rule_applies_to(rule: dict, h: dict) -> bool:
    if rule["token_id"] != "all":
        return h["token_id"] == rule["token_id"]
    owner = rule.get("created_by", "system")
    if owner in GLOBAL_RULE_ACTORS:
        return True
    return h.get("recommended_by") == owner


def _position_key(h: dict) -> str:
    """Identifies the current position in a token. Changes when a closed
    position is re-opened, so rules can fire again on the new position."""
    return h.get("position_opened_at") or "initial"


def evaluate_rules(prices_by_token: dict = None) -> list:
    """
    Check all enabled rules against current portfolio state.
    Returns list of triggered rule events (also executes them as paper trades).

    prices_by_token: optional pre-fetched prices dict. If None, fetches fresh.
    """
    rules = load_rules()
    enabled_rules = [r for r in rules if r.get("enabled", True)]
    if not enabled_rules:
        return evaluate_risk_stops()

    if prices_by_token is None:
        prices_by_token = fetch_all_token_prices()

    triggered = []
    rules_changed = False

    for rule in enabled_rules:
        # Recompute holdings for every rule: an earlier rule in this same run
        # may already have sold part of a position. (Previously one stale
        # snapshot was shared, so two "sell 50%" rules sold 100%.)
        summary = compute_summary()
        applicable = [h for h in summary["holdings"] if _rule_applies_to(rule, h)]

        for h in applicable:
            if h["amount"] <= 0 or h["cost_basis_usd"] <= 0:
                continue
            # Fire at most once per position. After a proportional partial sell
            # the P&L % is unchanged, so without this the same take-profit
            # re-fired on every run until the position was gone.
            position_key = _position_key(h)
            if rule.get("fired", {}).get(h["token_id"]) == position_key:
                continue
            pnl_pct = h["pnl_pct"]
            threshold = rule["threshold"]
            rule_type = rule["type"]

            should_trigger = False
            if rule_type == "take_profit" and pnl_pct >= threshold:
                should_trigger = True
            elif rule_type == "stop_loss" and pnl_pct <= threshold:
                should_trigger = True
            # (trailing_stop and rebalance not yet implemented)

            if not should_trigger:
                continue

            # Execute the action
            action = rule["action"]
            pct_to_sell = _pct_to_sell(action)

            if pct_to_sell > 0 and h["amount"] > 0:
                amount_to_sell = h["amount"] * (pct_to_sell / 100.0)
                price = h["last_price"]
                if price and price > 0:
                    try:
                        trade = record_trade(
                            action="SELL",
                            token_id=h["token_id"],
                            amount=amount_to_sell,
                            price_usd=price,
                            note=f"Auto: rule '{rule['id']}' triggered ({rule_type} @ {pnl_pct:+.2f}% → {action})",
                        )
                        event_data = {
                            "rule_id": rule["id"],
                            "rule_type": rule_type,
                            "token_id": h["token_id"],
                            "ticker": h["ticker"],
                            "pnl_pct_at_trigger": pnl_pct,
                            "threshold": threshold,
                            "action": action,
                            "trade_id": trade["id"],
                            "amount_sold": amount_to_sell,
                            "price_usd": price,
                        }
                        log_event(rule.get("created_by", "system"), "rule_triggered",
                                  f"Rule '{rule['id']}' triggered: {rule_type} on {h['ticker']} at {pnl_pct:+.2f}% P&L → {action}",
                                  event_data)
                        triggered.append(event_data)
                        rule.setdefault("fired", {})[h["token_id"]] = position_key
                        rules_changed = True
                    except Exception as e:
                        log_event("system", "rule_error",
                                  f"Failed to execute rule '{rule['id']}' on {h['ticker']}: {e}",
                                  {"rule_id": rule["id"], "error": str(e)})
            else:
                # alert_only — just log
                event_data = {
                    "rule_id": rule["id"],
                    "rule_type": rule_type,
                    "token_id": h["token_id"],
                    "ticker": h["ticker"],
                    "pnl_pct_at_trigger": pnl_pct,
                    "threshold": threshold,
                    "action": action,
                }
                log_event(rule.get("created_by", "system"), "rule_triggered",
                          f"Alert: rule '{rule['id']}' triggered ({rule_type} on {h['ticker']} at {pnl_pct:+.2f}%)",
                          event_data)
                triggered.append(event_data)
                rule.setdefault("fired", {})[h["token_id"]] = position_key
                rules_changed = True

    if rules_changed:
        # enabled_rules holds the same dicts as rules, so the "fired" marks persist.
        save_rules(rules)

    # Stop-loss / re-entry rules for hand-picked tokens (data/risk.json)
    triggered.extend(evaluate_risk_stops())
    return triggered


if __name__ == "__main__":
    print("Evaluating rules...")
    triggered = evaluate_rules()
    if triggered:
        print(f"\n{len(triggered)} rule(s) triggered:")
        for t in triggered:
            print(f"  - {t['rule_id']}: {t['rule_type']} on {t['ticker']} at {t['pnl_pct_at_trigger']:+.2f}% → {t['action']}")
    else:
        print("\nNo rules triggered.")
