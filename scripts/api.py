#!/usr/bin/env python3
"""
GitHub-backed API for AI agents and humans to interact with the paper trading portfolio.

This script provides a unified interface for:
  - Reading portfolio state, trades, price history, rules, activity log
  - Adding new paper trades (BUY/SELL)
  - Adding/removing automated trading rules
  - Triggering a price refresh (locally or via GitHub Actions workflow_dispatch)
  - Querying system status

It works in TWO modes:

1. **Local mode** (default): reads/writes data/ files directly. Use this when
   running on the same machine as the data files (e.g., from the cron, or
   when you cloned the repo locally).

2. **Remote mode** (--remote): uses the GitHub REST API to read/write the
   JSON files in the GitHub repo directly. Use this when an AI agent (or
   the user) wants to interact with the live portfolio from anywhere, without
   cloning the repo first.

Usage examples:

  # Local mode — get portfolio summary
  python scripts/api.py status

  # Local mode — add a paper buy
  python scripts/api.py buy tendies --usd 5 --actor zAI --note "doubling down"

  # Local mode — add a take-profit rule
  python scripts/api.py add-rule --token-id fox --type take_profit --threshold 200 --action sell_50pct --actor zAI

  # Remote mode (uses GitHub API) — get portfolio summary from anywhere
  python scripts/api.py --remote status

  # Remote mode — trigger a refresh on the GitHub Actions cron
  python scripts/api.py --remote refresh

Environment variables for remote mode:
  GITHUB_TOKEN    Personal access token with `repo` scope (required)
  GITHUB_REPO     Repo in `owner/name` format (default: SonaMother/crypto-paper-trader)

Remote mode uses the GitHub REST API:
  GET  /repos/{owner}/{repo}/contents/{path}    → read file (returns base64 content)
  PUT  /repos/{owner}/{repo}/contents/{path}    → write file (requires SHA for updates)
  POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches  → trigger cron
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from portfolio import load_portfolio, save_portfolio, record_trade, compute_summary, load_trades, load_price_history
from rules_engine import load_rules, add_rule, remove_rule, evaluate_rules
from activity_log import log_event, get_system_status, get_recent_events
from fetch_prices import fetch_all_token_prices
from utils import now_iso

GITHUB_API = "https://api.github.com"

DATA_FILES = {
    "portfolio": "data/portfolio.json",
    "trades": "data/trades.json",
    "price_history": "data/price_history.json",
    "rules": "data/rules.json",
    "activity_log": "data/activity_log.json",
}


# ============================================================================
# Local mode (direct file access)
# ============================================================================

def local_status():
    summary = compute_summary()
    status = get_system_status()
    return {
        "portfolio": summary,
        "system_status": status,
        "timestamp": now_iso(),
    }


def local_buy(token_id: str, usd: float = None, amount: float = None, actor: str = "human", note: str = "",
              exceptional: bool = False):
    prices = fetch_all_token_prices()
    info = prices.get(token_id)
    if not info or info.get("price") is None:
        raise RuntimeError(f"Could not fetch price for {token_id}")
    price = info["price"]

    if usd is not None:
        amount_to_buy = usd / price
        usd_value = usd
    elif amount is not None:
        amount_to_buy = amount
        usd_value = amount * price
    else:
        raise ValueError("Must specify --usd or --amount")

    # Risk rules (data/risk.json) for hand-picked tokens. exceptional=True allows
    # the larger max position size; it is recorded in the activity log.
    from risk import check_buy
    check_buy(token_id, usd_value, exceptional=exceptional)
    if exceptional:
        note = (note + " " if note else "") + "[exceptional size]"

    # Fund the paper account for this buy
    p = load_portfolio()
    p["cash_usd"] = p.get("cash_usd", 0) + usd_value
    save_portfolio(p)

    trade = record_trade("BUY", token_id, amount_to_buy, price, note=note or f"API buy by {actor}: ${usd_value:.2f} of {token_id}")
    log_event(actor, "buy", f"{actor} bought {amount_to_buy:.6f} {trade['ticker']} @ ${price:.6g} (${usd_value:.2f})",
              {"trade_id": trade["id"], "token_id": token_id, "amount": amount_to_buy, "price_usd": price, "usd_value": usd_value})
    return trade


def local_sell(token_id: str, pct: float = None, usd: float = None, amount: float = None, actor: str = "human", note: str = "",
               force: bool = False):
    prices = fetch_all_token_prices()
    info = prices.get(token_id)
    if not info or info.get("price") is None:
        raise RuntimeError(f"Could not fetch price for {token_id}")
    price = info["price"]

    p = load_portfolio()
    h = p["holdings"].get(token_id)
    if not h or h["amount"] <= 0:
        raise RuntimeError(f"No holdings to sell for {token_id}")

    if pct is not None:
        amount_to_sell = h["amount"] * (pct / 100.0)
    elif usd is not None:
        amount_to_sell = usd / price
    elif amount is not None:
        amount_to_sell = amount
    else:
        raise ValueError("Must specify --pct, --usd, or --amount")

    if amount_to_sell > h["amount"]:
        raise RuntimeError(f"Insufficient balance: have {h['amount']}, want to sell {amount_to_sell}")

    # Hold-until-break-even rule on re-entry positions. force=True overrides it;
    # the override is recorded in the trade note and activity log.
    if force:
        note = (note + " " if note else "") + "[forced: hold-until-break-even overridden]"
    else:
        from risk import check_sell
        check_sell(token_id, price)

    trade = record_trade("SELL", token_id, amount_to_sell, price, note=note or f"API sell by {actor}: {amount_to_sell:.6f} of {token_id}")
    log_event(actor, "sell", f"{actor} sold {amount_to_sell:.6f} {trade['ticker']} @ ${price:.6g} (${amount_to_sell * price:.2f})",
              {"trade_id": trade["id"], "token_id": token_id, "amount": amount_to_sell, "price_usd": price})
    return trade


def local_refresh():
    """Fetch fresh prices, update portfolio, evaluate rules, log the event."""
    from portfolio import update_prices
    prices = fetch_all_token_prices()
    fetched = sum(1 for p in prices.values() if p.get("price") is not None)
    total = len(prices)
    update_prices(prices)
    triggered = evaluate_rules(prices)
    log_event("cron", "refresh", f"Refreshed prices ({fetched}/{total} fetched). {len(triggered)} rule(s) triggered.",
              {"fetched": fetched, "total": total, "rules_triggered": len(triggered)})
    summary = compute_summary()
    return {"fetched": fetched, "total": total, "rules_triggered": len(triggered), "summary": summary}


def local_add_rule(token_id: str, rule_type: str, threshold: float, action: str, actor: str = "human", note: str = ""):
    rule = {
        "token_id": token_id,
        "type": rule_type,
        "threshold": threshold,
        "action": action,
        "created_by": actor,
        "note": note,
    }
    return add_rule(rule)


def local_remove_rule(rule_id: str):
    success = remove_rule(rule_id)
    return {"removed": success, "rule_id": rule_id}


def local_list_rules():
    return load_rules()


def local_activity(limit: int = 50):
    return get_recent_events(limit)


# ============================================================================
# Remote mode (GitHub REST API)
# ============================================================================

def _gh_headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "paper-trader-api/1.0",
    }


def _gh_get(token: str, repo: str, path: str) -> dict:
    """GET a file's contents from GitHub. Returns {content, sha}."""
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    req = urllib.request.Request(url, headers=_gh_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = base64.b64decode(data["content"]).decode("utf-8")
        return {"content": content, "sha": data["sha"]}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"content": None, "sha": None}
        raise


def _gh_put(token: str, repo: str, path: str, content: str, sha: str = None, message: str = "update") -> dict:
    """PUT a file's contents to GitHub. Creates or updates."""
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_gh_headers(token),
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gh_dispatch_workflow(token: str, repo: str, workflow_filename: str = "update.yml") -> dict:
    """Trigger a workflow_dispatch event for the given workflow file."""
    url = f"{GITHUB_API}/repos/{repo}/actions/workflows/{workflow_filename}/dispatches"
    payload = {"ref": "main"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_gh_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {"dispatched": True, "status": resp.status}
    except urllib.error.HTTPError as e:
        return {"dispatched": False, "status": e.code, "error": e.read().decode("utf-8")}


def remote_status(token: str, repo: str) -> dict:
    """Read portfolio + trades + activity log from GitHub."""
    out = {"timestamp": now_iso(), "repo": repo}
    for key, path in DATA_FILES.items():
        result = _gh_get(token, repo, path)
        content = result.get("content")
        if content is None:
            out[key] = None
        else:
            try:
                out[key] = json.loads(content)
            except json.JSONDecodeError:
                out[key] = None
    return out


def remote_refresh(token: str, repo: str) -> dict:
    """Trigger the GitHub Actions workflow to run a refresh."""
    return _gh_dispatch_workflow(token, repo)


def remote_buy(token_id: str, usd: float = None, amount: float = None, actor: str = "ai", note: str = "", gh_token: str = None, repo: str = None,
               exceptional: bool = False):
    """
    Add a paper buy via the GitHub API.

    This is more complex than local mode because we need to:
    1. Read the current portfolio.json + trades.json from GitHub
    2. Fetch a live price
    3. Compute the new state
    4. Write both files back to GitHub

    For simplicity, we delegate the actual computation to a Python helper
    by writing a "pending action" file, which the next cron run will pick up.

    OR, we do it all here by temporarily pulling the files into a temp dir,
    running the local logic, and pushing back. We'll do the latter.
    """
    import tempfile, subprocess
    with tempfile.TemporaryDirectory() as tmpdir:
        # Clone the repo (shallow)
        clone_url = f"https://{gh_token}@github.com/{repo}.git"
        subprocess.run(["git", "clone", "--depth", "1", clone_url, tmpdir], check=True, capture_output=True)
        # Add scripts dir to path and re-import
        sys.path.insert(0, os.path.join(tmpdir, "scripts"))
        # Force re-import of portfolio etc. from the cloned repo
        for mod_name in ["portfolio", "utils", "config", "activity_log", "rules_engine", "fetch_prices"]:
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        sys.path.insert(0, os.path.join(tmpdir, "scripts"))
        from portfolio import load_portfolio, save_portfolio, record_trade
        from fetch_prices import fetch_all_token_prices
        from activity_log import log_event
        from utils import now_iso

        # Change to the cloned dir so all the file paths resolve correctly
        os.chdir(tmpdir)

        prices = fetch_all_token_prices()
        info = prices.get(token_id)
        if not info or info.get("price") is None:
            raise RuntimeError(f"Could not fetch price for {token_id}")
        price = info["price"]

        if usd is not None:
            amount_to_buy = usd / price
            usd_value = usd
        elif amount is not None:
            amount_to_buy = amount
            usd_value = amount * price
        else:
            raise ValueError("Must specify --usd or --amount")

        # Risk rules for hand-picked tokens (same as local_buy)
        from risk import check_buy
        check_buy(token_id, usd_value, exceptional=exceptional)
        if exceptional:
            note = (note + " " if note else "") + "[exceptional size]"

        # Fund + record
        p = load_portfolio()
        p["cash_usd"] = p.get("cash_usd", 0) + usd_value
        save_portfolio(p)
        trade = record_trade("BUY", token_id, amount_to_buy, price, note=note or f"Remote API buy by {actor}")
        log_event(actor, "buy", f"{actor} bought {amount_to_buy:.6f} {trade['ticker']} @ ${price:.6g} (${usd_value:.2f})",
                  {"trade_id": trade["id"], "token_id": token_id, "amount": amount_to_buy, "price_usd": price, "usd_value": usd_value, "via": "remote_api"})

        # Commit and push
        subprocess.run(["git", "config", "user.email", f"{actor}@paper-trader"], check=True)
        subprocess.run(["git", "config", "user.name", actor], check=True)
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-m", f"api({actor}): buy {amount_to_buy:.6f} {trade['ticker']} @ ${price:.6g}"], check=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        return {"trade": trade, "pushed": True}


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Paper trading portfolio API (local + remote)")
    parser.add_argument("--remote", action="store_true", help="Use GitHub REST API instead of local files")
    parser.add_argument("--token", help="GitHub PAT (or set GITHUB_TOKEN env var)")
    parser.add_argument("--repo", help="GitHub repo in owner/name format (or set GITHUB_REPO env var)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Show portfolio summary + system status")
    p_buy = sub.add_parser("buy", help="Buy a token")
    p_buy.add_argument("token_id")
    p_buy.add_argument("--usd", type=float)
    p_buy.add_argument("--amount", type=float)
    p_buy.add_argument("--actor", default="human", help="Who is making this buy (zAI, gAi, human, etc.)")
    p_buy.add_argument("--note", default="")
    p_buy.add_argument("--exceptional", action="store_true",
                       help="Exceptional setup: allows the larger max position size from data/risk.json")

    p_sell = sub.add_parser("sell", help="Sell a token")
    p_sell.add_argument("token_id")
    p_sell.add_argument("--pct", type=float)
    p_sell.add_argument("--usd", type=float)
    p_sell.add_argument("--amount", type=float)
    p_sell.add_argument("--actor", default="human")
    p_sell.add_argument("--note", default="")
    p_sell.add_argument("--force", action="store_true",
                        help="Override the hold-until-break-even rule on a re-entry position")

    p_refresh = sub.add_parser("refresh", help="Fetch fresh prices and update portfolio")
    p_rule = sub.add_parser("rule", help="Manage automated trading rules")
    p_rule_sub = p_rule.add_subparsers(dest="rule_cmd", required=True)
    p_rule_add = p_rule_sub.add_parser("add", help="Add a rule")
    p_rule_add.add_argument("--token-id", required=True)
    p_rule_add.add_argument("--type", required=True, choices=["take_profit", "stop_loss"])
    p_rule_add.add_argument("--threshold", type=float, required=True, help="P&L %% threshold (e.g. 100 for 2x, -50 for 50%% loss)")
    p_rule_add.add_argument("--action", required=True, choices=["sell_25pct", "sell_50pct", "sell_75pct", "sell_100pct", "alert_only"])
    p_rule_add.add_argument("--actor", default="human")
    p_rule_add.add_argument("--note", default="")
    p_rule_remove = p_rule_sub.add_parser("remove", help="Remove a rule by id")
    p_rule_remove.add_argument("rule_id")
    p_rule_list = p_rule_sub.add_parser("list", help="List all rules")

    p_activity = sub.add_parser("activity", help="Show recent activity log")
    p_activity.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()

    # Resolve remote config
    if args.remote:
        gh_token = args.token or os.environ.get("GITHUB_TOKEN")
        gh_repo = args.repo or os.environ.get("GITHUB_REPO", "SonaMother/crypto-paper-trader")
        if not gh_token:
            print("Error: --token or GITHUB_TOKEN env var required for remote mode", file=sys.stderr)
            sys.exit(1)

    # Dispatch
    if args.cmd == "status":
        if args.remote:
            result = remote_status(gh_token, gh_repo)
        else:
            result = local_status()
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "buy":
        if args.remote:
            result = remote_buy(args.token_id, usd=args.usd, amount=args.amount, actor=args.actor, note=args.note, gh_token=gh_token, repo=gh_repo,
                                exceptional=args.exceptional)
        else:
            result = local_buy(args.token_id, usd=args.usd, amount=args.amount, actor=args.actor, note=args.note,
                               exceptional=args.exceptional)
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "sell":
        if args.remote:
            print("Remote sell not yet implemented — use local mode or trigger via workflow", file=sys.stderr)
            sys.exit(1)
        result = local_sell(args.token_id, pct=args.pct, usd=args.usd, amount=args.amount, actor=args.actor, note=args.note,
                            force=args.force)
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "refresh":
        if args.remote:
            result = remote_refresh(gh_token, gh_repo)
        else:
            result = local_refresh()
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "rule":
        if args.remote:
            print("Remote rule management not yet implemented — use local mode", file=sys.stderr)
            sys.exit(1)
        if args.rule_cmd == "add":
            result = local_add_rule(args.token_id, args.type, args.threshold, args.action, args.actor, args.note)
        elif args.rule_cmd == "remove":
            result = local_remove_rule(args.rule_id)
        elif args.rule_cmd == "list":
            result = local_list_rules()
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "activity":
        result = local_activity(args.limit)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
