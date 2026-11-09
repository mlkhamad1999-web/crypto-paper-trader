"""
Initialize the paper portfolio by buying $1 of each tracked token at current prices.

If called with no args, it will:
  - Initialize the portfolio from scratch if no holdings exist
  - Buy any NEW tokens that have been added to config.py but don't have a position yet
    (idempotent - safe to run after adding new tokens to config.py)

Use --force to wipe everything and re-initialize from scratch.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import TOKENS as ALL_TOKENS, INITIAL_BUY_USD

# Custom tokens (added by contract address) are bought by hand, never by init.
TOKENS = [t for t in ALL_TOKENS if not t.get("custom")]
from fetch_prices import fetch_all_token_prices
from portfolio import (
    load_portfolio, empty_portfolio, save_portfolio,
    load_trades, save_trades, load_price_history, save_price_history,
    record_trade, update_prices,
)
from utils import now_iso


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Wipe existing data and re-initialize")
    args = parser.parse_args()

    existing = load_portfolio()
    has_holdings = bool(existing.get("holdings"))

    if has_holdings and not args.force:
        # Smart init: only buy tokens that don't have a position yet
        new_tokens = []
        for t in TOKENS:
            if t["id"] not in existing["holdings"] or existing["holdings"][t["id"]]["amount"] == 0:
                new_tokens.append(t)

        if not new_tokens:
            print("All tokens already have positions. Nothing to do.")
            print("Use --force to wipe and re-initialize from scratch.")
            return

        print(f"Found {len(new_tokens)} new token(s) to buy: {[t['ticker'] for t in new_tokens]}")
        # Fund the paper account for the new buys
        capital_needed = INITIAL_BUY_USD * len(new_tokens)
        p = load_portfolio()
        p["cash_usd"] = p.get("cash_usd", 0) + capital_needed
        save_portfolio(p)
        print(f"Funded paper account with additional ${capital_needed:.2f} USD")

        print("\nFetching current prices...")
        prices = fetch_all_token_prices()

        print("\nExecuting paper buys for new tokens:")
        print(f"{'Token':<10} {'Price (USD)':<18} {'Amount':<20} {'Status'}")
        print("-" * 70)
        for token in new_tokens:
            tid = token["id"]
            info = prices.get(tid, {})
            price = info.get("price") if info else None
            if price is None or price <= 0:
                print(f"{token['ticker']:<10} {'N/A':<18} {'N/A':<20} SKIP (price unavailable)")
                continue
            amount = INITIAL_BUY_USD / price
            trade = record_trade(
                action="BUY",
                token_id=tid,
                amount=amount,
                price_usd=price,
                note=f"Initial paper buy: ${INITIAL_BUY_USD:.2f} of {token['ticker']} (by {token.get('recommended_by', 'unknown')})",
            )
            print(f"{token['ticker']:<10} ${price:<17.8g} {amount:<20.6f} OK")

        print("\nMarking to market...")
        prices2 = fetch_all_token_prices()
        update_prices(prices2)
        print("\n✓ Done.")
        return

    if args.force:
        print("Wiping existing portfolio/trades/history...")
        save_portfolio(empty_portfolio())
        save_trades([])
        save_price_history([])

    # Fund the paper account with the total capital we'll deploy
    capital = INITIAL_BUY_USD * len(TOKENS)
    p = load_portfolio()
    p["cash_usd"] = capital
    p["created_at"] = now_iso()
    save_portfolio(p)
    print(f"Funded paper account with ${capital:.2f} USD")

    # Fetch current prices
    print("\nFetching current prices...")
    prices = fetch_all_token_prices()

    # Buy $1 of each token
    print("\nExecuting paper buys:")
    print(f"{'Token':<10} {'By':<5} {'Price (USD)':<18} {'Amount':<20} {'Status'}")
    print("-" * 75)
    for token in TOKENS:
        tid = token["id"]
        info = prices.get(tid, {})
        price = info.get("price") if info else None
        if price is None or price <= 0:
            print(f"{token['ticker']:<10} {token.get('recommended_by', '?'):<5} {'N/A':<18} {'N/A':<20} SKIP (price unavailable)")
            continue
        amount = INITIAL_BUY_USD / price
        trade = record_trade(
            action="BUY",
            token_id=tid,
            amount=amount,
            price_usd=price,
            note=f"Initial paper buy: ${INITIAL_BUY_USD:.2f} of {token['ticker']} (by {token.get('recommended_by', 'unknown')})",
        )
        print(f"{token['ticker']:<10} {token.get('recommended_by', '?'):<5} ${price:<17.8g} {amount:<20.6f} OK")

    # Update prices one more time so portfolio.json reflects fresh mark-to-market
    print("\nMarking to market...")
    prices2 = fetch_all_token_prices()
    update_prices(prices2)

    print("\n✓ Portfolio initialized.")
    print(f"  Total deployed: ${INITIAL_BUY_USD * len(TOKENS):.2f}")
    print("  Run `python scripts/update_portfolio.py` anytime to refresh prices.")


if __name__ == "__main__":
    main()
