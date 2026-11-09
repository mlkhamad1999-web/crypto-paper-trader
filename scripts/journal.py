"""
Performance journal: do YOUR entries beat random entries?

    python scripts/trade.py journal          # hand-picked tokens (risk-governed)
    python scripts/trade.py journal --all    # every token, AI picks included

Rebuilds each position from trades.json (first buy -> fully sold), computes
its P&L after costs, and compares the average with the random-entry baseline
from scripts/backtest.py (same exit rules, entries with no edge). Exit rules
alone lose money on random entries, so the only way to be profitable is to pick
better entries than chance. This report tells you whether you do, and whether
you have enough trades to know.
"""

import math
import statistics
from datetime import datetime, timezone

from config import TOKENS
from portfolio import load_trades, load_portfolio
from risk import is_governed, load_risk_config

# scripts/backtest.py, Nov 2026, stop -15% + trailing 30%, costs 2.5%/side + 0.05$/tx.
# (label, mean % per trade, win rate %)
BASELINES = [
    ("PumpSwap (gradués), horizon 6h", -3.6, 29.5),
    ("Bonding curve pump.fun, horizon 2h", -9.3, 12.7),
]
MIN_TRADES = 30


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _exit_reason(note: str) -> str:
    n = note or ""
    if "risk-trailing-stop" in n:
        return "trailing stop"
    if "risk-stop-loss" in n or "risk-reentry-hard-stop" in n:
        return "stop-loss"
    if n.startswith("Auto: rule"):
        return "rule (take-profit / stop)"
    if "API sell" in n or "Remote API" in n:
        return "API"
    return "manual"


def build_positions(include_all: bool = False) -> list:
    cfg = load_risk_config()
    p = load_portfolio()
    tickers = {t["id"]: t["ticker"] for t in TOKENS}
    open_pos, done = {}, []
    for t in load_trades():
        tid = t["token_id"]
        if not include_all and not is_governed(tid, cfg):
            continue
        pos = open_pos.get(tid)
        if t["action"] == "BUY":
            if pos is None:
                pos = open_pos[tid] = {"token_id": tid, "ticker": tickers.get(tid, tid), "opened": t["ts"],
                                       "invested": 0.0, "proceeds": 0.0, "qty": 0.0, "max_qty": 0.0,
                                       "closed": None, "reason": None}
            pos["invested"] += t["value_usd"]
            pos["qty"] += t["amount"]
            pos["max_qty"] = max(pos["max_qty"], pos["qty"])
        elif pos is not None:
            pos["proceeds"] += t["value_usd"]
            pos["qty"] -= t["amount"]
            pos["reason"] = _exit_reason(t.get("note", ""))
            if pos["qty"] <= pos["max_qty"] * 1e-9:
                pos["closed"] = t["ts"]
                done.append(open_pos.pop(tid))
    now = datetime.now(timezone.utc)
    for tid, pos in open_pos.items():
        price = (p["holdings"].get(tid) or {}).get("last_price") or 0
        pos["open_value"] = pos["qty"] * price
        done.append(pos)
    for pos in done:
        pos.setdefault("open_value", 0.0)
        pos["pnl"] = pos["proceeds"] + pos["open_value"] - pos["invested"]
        pos["pct"] = pos["pnl"] / pos["invested"] * 100 if pos["invested"] else 0.0
        end = _ts(pos["closed"]) if pos["closed"] else now
        pos["hours"] = (end - _ts(pos["opened"])).total_seconds() / 3600
    return sorted(done, key=lambda x: x["opened"])


def print_journal(include_all: bool = False) -> None:
    positions = build_positions(include_all)
    scope = "tous les tokens" if include_all else "tokens choisis à la main (règles de risque)"
    print(f"Journal de performance — {scope}")
    if not positions:
        print("  Aucun trade encore. Ajoute un token avec `trade.py add-token <CA>` puis `trade.py buy`.")
        return

    print(f"\n  {'Ouvert le (UTC)':<17} {'Token':<9} {'Investi':>8} {'P&L $':>8} {'P&L %':>8} {'Durée':>7}  Sortie")
    for x in positions[-25:]:
        status = x["reason"] if x["closed"] else "OUVERTE"
        print(f"  {x['opened'][:16].replace('T', ' '):<17} {x['ticker'][:9]:<9} {x['invested']:>8.2f} "
              f"{x['pnl']:>+8.2f} {x['pct']:>+7.1f}% {x['hours']:>6.1f}h  {status}")

    closed = [x for x in positions if x["closed"]]
    if not closed:
        print("\n  Aucune position clôturée encore.")
        return
    pcts = [x["pct"] for x in closed]
    n = len(pcts)
    mean = statistics.mean(pcts)
    win = sum(p > 0 for p in pcts) / n * 100
    print(f"\n  Positions clôturées : {n}")
    print(f"  Gagnantes          : {win:.0f}%")
    print(f"  P&L moyen / trade  : {mean:+.1f}%   (médian {statistics.median(pcts):+.1f}%)")
    print(f"  P&L total          : {sum(x['pnl'] for x in closed):+.2f} $")
    print(f"  Meilleur / pire    : {max(pcts):+.1f}% / {min(pcts):+.1f}%")
    print(f"  Durée moyenne      : {statistics.mean(x['hours'] for x in closed):.1f}h")
    reasons = {}
    for x in closed:
        reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1
    print("  Sorties            : " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])))

    print("\n  Référence, entrées AU HASARD avec les mêmes règles (backtest) :")
    for label, b_mean, b_win in BASELINES:
        print(f"    {label:<36} {b_mean:+.1f}% / trade, {b_win:.0f}% gagnantes")

    best_baseline = max(b for _, b, _ in BASELINES)
    if n >= 2:
        half = 1.96 * statistics.stdev(pcts) / math.sqrt(n)
        lo, hi = mean - half, mean + half
        print(f"\n  Ta moyenne, intervalle de confiance 95% : {lo:+.1f}% à {hi:+.1f}%")
    else:
        lo = hi = mean
    print()
    if n < MIN_TRADES:
        print(f"  VERDICT : pas assez de trades pour conclure ({n}/{MIN_TRADES}). "
              "Continue en paper trading avant de risquer de l'argent réel.")
    elif lo > best_baseline and lo > 0:
        print("  VERDICT : tes entrées battent le hasard ET restent positives de façon significative. "
              "C'est un edge mesuré (en paper : le réel ajoute latence et slippage).")
    elif mean > best_baseline:
        print("  VERDICT : mieux que le hasard en moyenne, mais pas de façon significative. Continue à mesurer.")
    else:
        print("  VERDICT : pas mieux que des entrées au hasard. Pas d'edge démontré : ne passe pas en réel.")
