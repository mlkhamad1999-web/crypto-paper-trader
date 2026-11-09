"""
Manual trade CLI for the paper portfolio.

Examples:
    # Buy $5 of TENDIES at current market price
    python scripts/trade.py buy tendies --usd 5

    # Buy 100 FOX at current market price
    python scripts/trade.py buy fox --amount 100

    # Sell 50% of PUMP holdings
    python scripts/trade.py sell pump --pct 50

    # Sell $3 worth of CASHCAT
    python scripts/trade.py sell cashcat --usd 3

    # Track a token by contract address (Solana by default), priced via GeckoTerminal
    python scripts/trade.py add-token <CONTRACT_ADDRESS>

    # Buy a hand-picked token: risk rules in data/risk.json are enforced
    python scripts/trade.py buy <token_id> --usd 5
    python scripts/trade.py buy <token_id> --usd 8 --exceptional

    # Show risk state (daily realized P&L, capital deployed, stops / re-entries)
    python scripts/trade.py risk
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import TOKENS
from fetch_prices import fetch_all_token_prices, fetch_geckoterminal_token_info, GECKOTERMINAL_NETWORKS
from portfolio import load_portfolio, save_portfolio, record_trade, compute_summary
from risk import RiskViolation, check_buy, check_sell, risk_status
import journal
import token_check
from utils import fmt_usd, fmt_pct, read_json, write_json, now_iso, CUSTOM_TOKENS_PATH

CUSTOM_COLORS = ["#f97316", "#84cc16", "#eab308", "#14b8a6", "#a855f7", "#ef4444", "#22c55e", "#0ea5e9"]


def get_price(token_id: str) -> float:
    prices = fetch_all_token_prices()
    info = prices.get(token_id)
    if not info or info.get("price") is None:
        raise RuntimeError(f"Could not fetch live price for {token_id}")
    return info["price"]


def cmd_buy(args):
    token_id = args.token.lower()
    token = next((t for t in TOKENS if t["id"] == token_id), None)
    if not token:
        print(f"Unknown token: {token_id}")
        print(f"Available: {', '.join(t['id'] for t in TOKENS)}")
        sys.exit(1)

    price = get_price(token_id)
    if args.usd is not None:
        usd = args.usd
        amount = usd / price
    elif args.amount is not None:
        amount = args.amount
        usd = amount * price
    else:
        print("Must specify --usd or --amount")
        sys.exit(1)

    try:
        for w in check_buy(token_id, usd, exceptional=args.exceptional):
            print(f"! {w}")
    except RiskViolation as e:
        print(f"✗ Refused by risk rules: {e}")
        sys.exit(1)

    # Fund the paper account for this buy (same convention as api.py and
    # init_portfolio.py), so cash doesn't go negative and distort total P&L.
    p = load_portfolio()
    p["cash_usd"] = p.get("cash_usd", 0) + usd
    save_portfolio(p)

    trade = record_trade("BUY", token_id, amount, price, note=args.note or f"Manual buy: {fmt_usd(usd)} of {token['ticker']}")
    print(f"✓ BUY {trade['amount']:.6f} {token['ticker']} @ ${trade['price_usd']:.8g} = {fmt_usd(trade['value_usd'])}")
    print(f"  Trade ID: {trade['id']}")


def cmd_sell(args):
    token_id = args.token.lower()
    token = next((t for t in TOKENS if t["id"] == token_id), None)
    if not token:
        print(f"Unknown token: {token_id}")
        sys.exit(1)

    p = load_portfolio()
    h = p["holdings"].get(token_id)
    if not h or h["amount"] <= 0:
        print(f"No holdings to sell for {token_id}")
        sys.exit(1)

    price = get_price(token_id)
    if args.pct is not None:
        amount = h["amount"] * (args.pct / 100.0)
    elif args.usd is not None:
        amount = args.usd / price
    elif args.amount is not None:
        amount = args.amount
    else:
        print("Must specify --pct, --usd, or --amount")
        sys.exit(1)

    if amount > h["amount"]:
        print(f"Insufficient balance: have {h['amount']}, want to sell {amount}")
        sys.exit(1)

    if not args.force:
        try:
            check_sell(token_id, price)
        except RiskViolation as e:
            print(f"✗ Refused by risk rules: {e}")
            sys.exit(1)

    trade = record_trade("SELL", token_id, amount, price, note=args.note or f"Manual sell: {amount:.6f} {token['ticker']}")
    print(f"✓ SELL {trade['amount']:.6f} {token['ticker']} @ ${trade['price_usd']:.8g} = {fmt_usd(trade['value_usd'])}"
          f"  (realized {fmt_usd(trade.get('realized_pnl_usd', 0))})")
    print(f"  Trade ID: {trade['id']}")


def cmd_add_token(args):
    address = args.address.strip()
    if any(t.get("contract") == address for t in TOKENS):
        print(f"Already tracked: {address}")
        sys.exit(1)
    try:
        info = fetch_geckoterminal_token_info(args.chain, address)
    except Exception as e:
        print(f"✗ Could not look up {address} on GeckoTerminal: {e}")
        sys.exit(1)

    # Safety check first (Solana only): a major red flag = refused, per your rules.
    safety = None
    if args.chain == "Solana":
        try:
            safety = token_check.check(address)
            token_check.print_report(address, safety)
            print()
        except Exception as e:
            print(f"! Safety check unavailable ({e}). Run `trade.py check {address}` later.")
        if safety and safety["red"] and not args.ignore_red_flags:
            print("✗ Not added: major red flag(s). Use --ignore-red-flags to track it anyway.")
            sys.exit(1)

    existing_ids = {t["id"] for t in TOKENS}
    base = args.id or re.sub(r"[^a-z0-9]", "", info["symbol"].lower()) or "token"
    token_id = base if base not in existing_ids else f"{base}-{address[:4].lower()}"
    if token_id in existing_ids:
        print(f"✗ Token id '{token_id}' already exists; pass --id")
        sys.exit(1)

    # Symbol and name are chosen by whoever launched the token and end up in the
    # dashboard HTML: keep only harmless characters.
    safe = lambda s, n: re.sub(r"[^A-Za-z0-9 ._\-]", "", s or "").strip()[:n]
    custom = read_json(CUSTOM_TOKENS_PATH, [])
    color = args.color if args.color and re.fullmatch(r"#[0-9a-fA-F]{6}", args.color) else None
    token = {
        "id": token_id,
        "ticker": safe(info["symbol"], 12).upper() or token_id.upper(),  # also drops the "$" of "$WIF"
        "name": safe(info["name"], 40) or token_id,
        "chain": args.chain,
        "coingecko_id": None,
        "contract": address,
        "color": color or CUSTOM_COLORS[len(custom) % len(CUSTOM_COLORS)],
        "recommended_by": "human",
        "added_at": now_iso(),
        "safety_at_add": ({"red": safety["red"], "warn": safety["warn"], "top10_pct": round(safety["top10"], 1),
                           "liquidity_usd": round(safety["liquidity"])} if safety else None),
        "note": args.note or "",
    }
    custom.append(token)
    write_json(CUSTOM_TOKENS_PATH, custom)
    print(f"✓ Tracking {token['ticker']} ({token['name']}) as '{token_id}' — {address}")
    print(f"  Current price: ${info['price']:.10g}")
    print(f"  Buy with: python scripts/trade.py buy {token_id} --usd 5")


def cmd_check(args):
    try:
        r = token_check.check(args.address.strip())
    except Exception as e:
        print(f"✗ Safety check failed: {e}")
        sys.exit(1)
    token_check.print_report(args.address.strip(), r)
    sys.exit(2 if r["red"] else 0)


def cmd_risk(args):
    s = risk_status()
    cfg = s["config"]
    print("Risk rules (data/risk.json, amounts in USD):")
    print(f"  enabled={cfg['enabled']}  applies_to={cfg['applies_to']}  timezone={cfg['timezone']}")
    print(f"  capital max {cfg['max_capital_usd']}  daily loss max {cfg['max_daily_loss_usd']}  "
          f"position {cfg['max_position_usd']} (exceptional {cfg['max_position_exceptional_usd']})")
    print(f"  stop {cfg['stop_loss_pct']}%  trailing {cfg.get('trailing_stop_pct') or 'off'}"
          f"{'%' if cfg.get('trailing_stop_pct') else ''}  one re-entry after stop: {cfg['allow_one_reentry_after_stop']}  "
          f"hold re-entry until break-even: {cfg['hold_reentry_until_breakeven']}  "
          f"re-entry hard stop: {cfg['reentry_hard_stop_pct']}")
    print()
    print(f"  Realized P&L today : {fmt_usd(s['realized_pnl_today_usd'])}"
          + ("   ← BUYS BLOCKED" if s['realized_pnl_today_usd'] <= -cfg['max_daily_loss_usd'] else ""))
    print(f"  Capital deployed   : {fmt_usd(s['deployed_usd'])} / {fmt_usd(cfg['max_capital_usd'])}")
    print()
    if not s["positions"]:
        print("  No governed tokens yet. Add one with: python scripts/trade.py add-token <CA>")
        return
    print(f"  {'Token':<10} {'Cost':<9} {'Value':<9} {'P&L':<9} {'Stops':<6} State")
    for pos in s["positions"]:
        value = pos["amount"] * (pos["last_price"] or 0)
        cost = pos["cost_basis_usd"]
        pnl = f"{(value - cost) / cost * 100:+.1f}%" if cost > 0 else "—"
        if pos["amount"] <= 0:
            state = "closed" + (" (no more entries)" if pos["reentry_used"] else
                                (" (1 re-entry left)" if pos["stops_hit"] else ""))
        else:
            if pos["is_reentry"] and cfg["hold_reentry_until_breakeven"]:
                state = "RE-ENTRY (hold to break-even)"
            else:
                state = ("RE-ENTRY, " if pos["is_reentry"] else "") + "open (stop active)"
        print(f"  {pos['ticker']:<10} {fmt_usd(cost):<9} {fmt_usd(value):<9} {pnl:<9} {pos['stops_hit']:<6} {state}")


def cmd_status(args):
    s = compute_summary()
    print(f"Portfolio summary (last updated {s['last_updated_at']}):")
    print(f"  Invested   : {fmt_usd(s['total_invested_usd'])}  (cashed out {fmt_usd(s['total_proceeds_usd'])},"
          f" open cost {fmt_usd(s['total_cost_usd'])})")
    print(f"  Total value: {fmt_usd(s['total_value_usd'])}")
    print(f"  P&L        : {fmt_usd(s['total_pnl_usd'])} ({fmt_pct(s['total_pnl_pct'])})")
    print(f"  Cash       : {fmt_usd(s['cash_usd'])}")
    print()
    print(f"{'Token':<10} {'Amount':<14} {'Cost':<10} {'Price':<14} {'Value':<10} {'P&L'}")
    print("-" * 70)
    for h in s["holdings"]:
        pnl = f"{fmt_usd(h['pnl_usd'])} ({fmt_pct(h['pnl_pct'])})"
        print(f"{h['ticker']:<10} {h['amount']:<14.4f} {fmt_usd(h['cost_basis_usd']):<10} "
              f"${h['last_price'] or 0:<13.8g} {fmt_usd(h['current_value_usd']):<10} {pnl}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paper trading portfolio CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_buy = sub.add_parser("buy", help="Buy a token with USD or specific amount")
    p_buy.add_argument("token", help="Token id (e.g. tendies)")
    p_buy.add_argument("--usd", type=float, help="USD worth to buy")
    p_buy.add_argument("--amount", type=float, help="Token amount to buy")
    p_buy.add_argument("--note", help="Optional trade note")
    p_buy.add_argument("--exceptional", action="store_true",
                       help="Exceptional setup: allows the larger max position size from data/risk.json")
    p_buy.set_defaults(func=cmd_buy)

    p_sell = sub.add_parser("sell", help="Sell a token by percentage/USD/amount")
    p_sell.add_argument("token", help="Token id")
    p_sell.add_argument("--pct", type=float, help="Percentage of holdings to sell (0-100)")
    p_sell.add_argument("--usd", type=float, help="USD worth to sell")
    p_sell.add_argument("--amount", type=float, help="Token amount to sell")
    p_sell.add_argument("--note", help="Optional trade note")
    p_sell.add_argument("--force", action="store_true",
                        help="Override the hold-until-break-even rule on a re-entry position")
    p_sell.set_defaults(func=cmd_sell)

    p_status = sub.add_parser("status", help="Show current portfolio status")
    p_status.set_defaults(func=cmd_status)

    p_add = sub.add_parser("add-token", help="Track a token by contract address (priced via GeckoTerminal)")
    p_add.add_argument("address", help="Token contract address (CA)")
    p_add.add_argument("--chain", default="Solana", choices=list(GECKOTERMINAL_NETWORKS),
                       help="Chain (default: Solana)")
    p_add.add_argument("--id", help="Internal id (default: derived from the symbol)")
    p_add.add_argument("--color", help="Dashboard color, e.g. '#ff8800'")
    p_add.add_argument("--note", help="Why you are tracking it")
    p_add.add_argument("--ignore-red-flags", action="store_true",
                       help="Track it even if the safety check finds a major red flag")
    p_add.set_defaults(func=cmd_add_token)

    p_check = sub.add_parser("check", help="Anti-scam safety check of a Solana token (RugCheck + GeckoTerminal)")
    p_check.add_argument("address", help="Token contract address (CA)")
    p_check.set_defaults(func=cmd_check)

    p_risk = sub.add_parser("risk", help="Show risk-rule state for hand-picked tokens")
    p_risk.set_defaults(func=cmd_risk)

    p_journal = sub.add_parser("journal", help="Your trade results vs random entries (do you have an edge?)")
    p_journal.add_argument("--all", action="store_true", help="Include every token (AI picks too)")
    p_journal.set_defaults(func=lambda a: journal.print_journal(a.all))

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
