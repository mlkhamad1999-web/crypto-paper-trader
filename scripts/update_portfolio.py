"""
Refresh all token prices, update the portfolio, evaluate trading rules,
and log the event to the activity log.

This is the script that GitHub Actions runs every 15 minutes.
Safe to run anytime locally as well.

Usage:
    python scripts/update_portfolio.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_prices import fetch_all_token_prices
from portfolio import update_prices, compute_summary
from rules_engine import evaluate_rules
from risk import recheck_open_positions
from activity_log import log_event
from utils import fmt_usd, fmt_pct, now_iso, write_json
from pathlib import Path

# Path to write a public health.json snapshot (read by the dashboard)
HEALTH_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "health.json")


def write_health_snapshot(summary: dict, fetched_count: int, total_count: int, rules_triggered: int) -> None:
    """Write a public health.json snapshot for the dashboard to read."""
    snapshot = {
        "timestamp": now_iso(),
        "overall_status": "healthy" if fetched_count > 0 else "degraded",
        "fetched_prices": fetched_count,
        "total_prices": total_count,
        "rules_triggered": rules_triggered,
        "portfolio_value_usd": summary["total_value_usd"],
        "portfolio_pnl_usd": summary["total_pnl_usd"],
        "portfolio_pnl_pct": summary["total_pnl_pct"],
        "per_ai": summary.get("per_ai", []),
    }
    write_json(HEALTH_JSON_PATH, snapshot)


def main():
    print("Fetching live prices from CoinGecko...")
    prices = fetch_all_token_prices()
    print("\nLive prices:")
    fetched_count = 0
    failed_count = 0
    for tid, info in prices.items():
        if info.get("price") is None:
            print(f"  {tid:10s}  UNAVAILABLE")
            failed_count += 1
        else:
            print(f"  {tid:10s}  ${info['price']:<14.8g}  24h: {info.get('change_24h', 0):+.2f}%")
            fetched_count += 1

    print(f"\nFetched: {fetched_count}/{fetched_count + failed_count}")

    if fetched_count == 0:
        print("\n[warn] No prices could be fetched (likely rate-limited).")
        print("[warn] Portfolio not updated this run.")
        print("[warn] The dashboard will still show cached prices + live browser-side prices.")
        log_event("cron", "refresh_failed", f"Refresh failed: 0/{fetched_count + failed_count} prices fetched",
                  {"fetched": 0, "total": fetched_count + failed_count})
        # Still write a health snapshot so the dashboard shows the failed run
        summary = compute_summary()
        write_health_snapshot(summary, 0, fetched_count + failed_count, 0)
        return

    print("\nUpdating portfolio...")
    update_prices(prices)

    print("\nEvaluating trading rules...")
    triggered = evaluate_rules(prices)
    if triggered:
        print(f"  {len(triggered)} rule(s) triggered:")
        for t in triggered:
            print(f"    - {t['rule_id']}: {t['rule_type']} on {t['ticker']} at {t['pnl_pct_at_trigger']:+.2f}% → {t['action']}")
    else:
        print("  No rules triggered.")

    print("\nSafety re-check of open hand-picked positions...")
    alerts = recheck_open_positions()
    for a in alerts:
        print(f"  ! {a['ticker']}: new red flag(s): {'; '.join(a['new_red_flags'])}"
              + ("  → SOLD" if a.get("sold") else ""))
    if not alerts:
        print("  No new red flags.")

    s = compute_summary()
    print("\nPortfolio summary:")
    print(f"  Total cost basis : {fmt_usd(s['total_cost_usd'])}")
    print(f"  Current value    : {fmt_usd(s['total_value_usd'])}")
    print(f"  P&L              : {fmt_usd(s['total_pnl_usd'])} ({fmt_pct(s['total_pnl_pct'])})")
    print(f"  Last updated     : {s['last_updated_at']}")
    print("\nPer-AI breakdown:")
    for ai in s.get('per_ai', []):
        print(f"  {ai['ai']}: {ai['num_tokens']} tokens, P&L = {fmt_usd(ai['pnl_usd'])} ({fmt_pct(ai['pnl_pct'])})")

    # Write public health snapshot for the dashboard
    write_health_snapshot(s, fetched_count, fetched_count + failed_count, len(triggered))

    # Log the refresh event
    log_event("cron", "refresh",
              f"Hourly refresh: {fetched_count}/{fetched_count + failed_count} prices fetched, {len(triggered)} rule(s) triggered. P&L: {fmt_usd(s['total_pnl_usd'])} ({fmt_pct(s['total_pnl_pct'])})",
              {"fetched": fetched_count, "total": fetched_count + failed_count, "rules_triggered": len(triggered),
               "portfolio_value": s['total_value_usd'], "pnl_pct": s['total_pnl_pct']})

    print("\n✓ Done.")


if __name__ == "__main__":
    main()
