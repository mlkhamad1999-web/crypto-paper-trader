"""End-to-end scenario of the risk rules (offline, prices are simulated).
Run through tests/run_all.py, which gives it a throwaway copy of the repo."""
import argparse, contextlib, io, json, os, sys

ROOT = sys.argv[1]
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

CUSTOM = [{"id": f"meme{c}", "ticker": f"MEME{c.upper()}", "name": f"Meme {c}", "chain": "Solana",
           "coingecko_id": None, "contract": f"FakeCA{c}111111111111111111111111111111", "color": "#f97316",
           "recommended_by": "human"} for c in "abcdefgh"]
json.dump(CUSTOM, open("data/custom_tokens.json", "w"))
# Sections A-G test the original rules (hold re-entry until break-even, no trailing)
RISK = json.load(open("data/risk.json"))
RISK.update({"trailing_stop_pct": None, "hold_reentry_until_breakeven": True, "safety_recheck_hours": None})
json.dump(RISK, open("data/risk.json", "w"))

import config, portfolio, risk, rules_engine, trade  # noqa: E402

PRICES = {}
trade.get_price = lambda tid: PRICES[tid]
FAILS = []


def expect(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def set_price(tid, p):
    PRICES[tid] = p
    portfolio.update_prices({tid: {"price": p}})


def run(fn, **kw):
    out = io.StringIO()
    ok = True
    with contextlib.redirect_stdout(out):
        try:
            fn(argparse.Namespace(**kw))
        except SystemExit:
            ok = False
    return ok, out.getvalue().strip()


def buy(tid, usd, exc=False):
    return run(trade.cmd_buy, token=tid, usd=usd, amount=None, note=None, exceptional=exc)


def sell(tid, pct=100, force=False):
    return run(trade.cmd_sell, token=tid, pct=pct, usd=None, amount=None, note=None, force=force)


def holding(tid):
    return portfolio.load_portfolio()["holdings"].get(tid, {})


def evaluate():
    with contextlib.redirect_stdout(io.StringIO()):
        return rules_engine.evaluate_rules({})


print("A. Position size")
for t in "abcdefgh":
    set_price(f"meme{t}", 1.0)
cash0 = portfolio.load_portfolio()["cash_usd"]
ok, out = buy("memea", 6); expect(not ok and "above the 5.00" in out, "buy 6 refused (max 5)")
ok, out = buy("memea", 5); expect(ok, "buy 5 accepted")
expect(abs(portfolio.load_portfolio()["cash_usd"] - cash0) < 1e-6, "manual buy is funded (cash unchanged net)")
ok, out = buy("memea", 3); expect(not ok and "--exceptional" in out, "adding 3 (total 8) refused without --exceptional")
ok, out = buy("memea", 3, exc=True); expect(ok, "adding 3 with --exceptional accepted (total 8)")
ok, out = buy("memea", 1, exc=True); expect(not ok, "adding 1 more refused (9 > 8)")

print("B. Stop-loss -15%")
set_price("memea", 0.86); ev = evaluate()
expect(not ev and holding("memea")["amount"] > 0, "-14% does not trigger the stop")
set_price("memea", 0.84); ev = evaluate()
expect(len(ev) == 1 and ev[0]["rule_id"] == "risk-stop-loss", "-16% triggers risk-stop-loss")
h = holding("memea")
expect(h["amount"] == 0 and h["stops_hit"] == 1, "position closed, stops_hit = 1")
last = portfolio.load_trades()[-1]
expect(abs(last["realized_pnl_usd"] - (-1.28)) < 1e-6, f"realized P&L recorded: {last['realized_pnl_usd']}")

print("C. Single re-entry, hold until break-even")
ok, out = buy("memea", 5); expect(ok and "ONE re-entry" in out, "re-entry accepted with warning")
expect(holding("memea")["is_reentry"] is True, "position flagged as re-entry")
set_price("memea", 0.42); ev = evaluate()
expect(not ev and holding("memea")["amount"] > 0, "re-entry at -50%: no stop")
ok, out = sell("memea"); expect(not ok and "break-even" in out, "manual sell below break-even refused")
set_price("memea", 0.85); ev = evaluate()
expect(len(ev) == 1 and ev[0]["rule_id"] == "risk-reentry-breakeven", "alert once back above break-even")
ev = evaluate(); expect(not ev, "no repeated break-even alert")
ok, out = sell("memea"); expect(ok, "sell allowed at/after break-even")
ok, out = buy("memea", 1); expect(not ok and "single re-entry was used" in out, "third entry refused")

print("D. Capital cap 50")
for t in "bcdef":
    ok, _ = buy(f"meme{t}", 8, exc=True); expect(ok, f"buy meme{t} 8")
ok, _ = buy("memeg", 8, exc=True); expect(ok, "buy memeg 8 (deployed 48)")
ok, out = buy("memeh", 5); expect(not ok and "Capital cap" in out, "buy 5 more refused (53 > 50)")

print("E. Daily loss limit 10")
for t in "bcdefg":
    set_price(f"meme{t}", 0.84)
ev = evaluate(); expect(len(ev) == 6, f"6 stops hit ({len(ev)})")
ok, out = buy("memeh", 5); expect(ok, "buy still allowed while loss < 10")
set_price("memeh", 0.70); ev = evaluate()
expect(risk.realized_pnl_today() <= -10, f"daily loss now beyond -10 ({risk.realized_pnl_today():+.2f})")
ok, out = buy("memeb", 5); expect(not ok and "Daily loss limit" in out, "all buys blocked for the day")

print("F. AI rules: owner filter + fire once")
before = len(portfolio.load_trades())
set_price("pump", 1.0)
ok, _ = buy("pump", 1); expect(ok, "buy PUMP (zAI pick, not governed by risk rules)")
set_price("pump", 2.1); ev = evaluate()
sells = [t for t in portfolio.load_trades()[before:] if t["token_id"] == "pump" and t["action"] == "SELL"]
expect(len(sells) == 1 and "tp-zai" in sells[0]["note"], "only zAI take-profit fires on a zAI pick")
expect(abs(holding("pump")["amount"] - 0.5) < 1e-6, "exactly 50% sold")
ev = evaluate()
sells = [t for t in portfolio.load_trades()[before:] if t["token_id"] == "pump" and t["action"] == "SELL"]
expect(len(sells) == 1, "take-profit does not re-fire on the next run")

print("G. record_trade safety")
n = len(portfolio.load_trades())
try:
    portfolio.record_trade("SELL", "lista", 999999, 1.0); raised = False
except ValueError:
    raised = True
expect(raised and len(portfolio.load_trades()) == n, "oversized sell rejected, no phantom trade written")
amt = holding("lista")["amount"]
portfolio.record_trade("SELL", "lista", amt + 5e-9, 0.07)
expect(holding("lista")["amount"] == 0, "rounding overshoot clamped to full balance")

print("H. Current defaults: trailing 30% + re-entry keeps the -15% stop")
RISK.update({"trailing_stop_pct": 30, "hold_reentry_until_breakeven": False, "max_daily_loss_usd": 1000})
json.dump(RISK, open("data/risk.json", "w"))
config.TOKENS.append({"id": "memez", "ticker": "MEMEZ", "name": "Meme z", "chain": "Solana", "coingecko_id": None,
                      "contract": "FakeCAz", "color": "#f97316", "recommended_by": "human", "custom": True})
set_price("memez", 1.0)
ok, _ = buy("memez", 5); expect(ok, "buy memez 5")
set_price("memez", 1.5); ev = evaluate(); expect(not ev, "rally to 1.5: no exit, peak recorded")
expect(holding("memez").get("peak_price") == 1.5, "peak_price = 1.5")
set_price("memez", 1.06); ev = evaluate(); expect(not ev, "1.06 (> 1.05 trail level): still open")
set_price("memez", 1.04); ev = evaluate()
expect(len(ev) == 1 and ev[0]["rule_id"] == "risk-trailing-stop", "1.04 (< 1.5 x 0.7): trailing stop exits")
expect(ev and ev[0]["realized_pnl_usd"] > 0, "exit in profit")
expect(holding("memez").get("stops_hit", 0) == 0, "profitable trailing exit is not a 'stop hit'")
ok, out = buy("memez", 5); expect(ok and "re-entry" not in out, "new normal entry allowed afterwards")
expect(holding("memez").get("peak_price") is None, "peak reset on new position")
set_price("memez", 1.0); set_price("memez", 0.84); ev = evaluate()
expect(ev and ev[0]["rule_id"] == "risk-stop-loss" and holding("memez")["stops_hit"] == 1, "-16%: stop-loss, stop hit")
ok, out = buy("memez", 5); expect(ok and "ONE re-entry" in out, "re-entry allowed once")
set_price("memez", 0.70); ev = evaluate()
expect(ev and ev[0]["rule_id"] == "risk-stop-loss", "re-entry is protected by the -15% stop")
ok, out = buy("memez", 5); expect(not ok, "no third entry")

print("I. Price history compaction")
hist = portfolio.load_price_history()
expect(portfolio.compact_history(hist) == hist, "recent history is kept intact")
now = 2_000_000_000
fake = [{"epoch": now - i * 900, "prices": {}, "portfolio_value_usd": 1} for i in range(200 * 96)][::-1]
c = portfolio.compact_history(fake, now)
expect(c[0] == fake[0] and len(c) < len(fake) / 3, f"200 days compacted to {len(c)} points, first point kept")
expect(portfolio.compact_history(c, now) == c, "compaction is idempotent")

print("RESULT:", "ALL PASS" if not FAILS else f"{len(FAILS)} FAIL(S): {FAILS}")
sys.exit(1 if FAILS else 0)
