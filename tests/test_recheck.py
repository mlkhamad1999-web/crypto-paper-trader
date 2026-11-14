"""Safety re-check of open positions, with a fake token_check (offline)."""
import contextlib, io, json, os, sys

ROOT = sys.argv[1]
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
json.dump([{"id": "memer", "ticker": "MEMER", "name": "r", "chain": "Solana", "coingecko_id": None,
            "contract": "FakeCAr", "color": "#f97316", "recommended_by": "human"}], open("data/custom_tokens.json", "w"))
cfg = json.load(open("data/risk.json"))
cfg.update({"safety_recheck_hours": 6, "exit_on_new_red_flag": False})
json.dump(cfg, open("data/risk.json", "w"))

import portfolio, risk, token_check  # noqa: E402

FAILS = []


def expect(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        FAILS.append(m)


RESULT = {}
token_check.check = lambda ca: RESULT["r"]


def flags(*reds):
    return {"flags": [("RED", r, "") for r in reds] + [("OK", "Mint authority revoked", "")], "red": len(reds), "warn": 0}


def force_due():
    p = portfolio.load_portfolio()
    p["holdings"]["memer"].pop("safety_checked_at", None)
    portfolio.save_portfolio(p)


def run():
    with contextlib.redirect_stdout(io.StringIO()):
        return risk.recheck_open_positions()


portfolio.record_trade("BUY", "memer", 5, 1.0)
portfolio.update_prices({"memer": {"price": 1.0}})  # the real cycle prices before re-checking
RESULT["r"] = flags()
expect(run() == [], "clean token: no alert")
expect(portfolio.load_portfolio()["holdings"]["memer"].get("safety_checked_at"), "check timestamp stored")
RESULT["r"] = flags("Liquidity 2,000$")
expect(run() == [], "not re-checked before 6h")
force_due(); a = run()
expect(len(a) == 1 and a[0]["new_red_flags"] == ["Liquidity 2,000$"], "new red flag -> alert")
log = json.load(open("data/activity_log.json"))
expect(any(e.get("action") == "safety_alert" for e in log), "alert written to the activity log (dashboard)")
RESULT["r"] = flags("Liquidity 1,500$")
force_due(); expect(run() == [], "same flag with a new number: no repeated alert")
expect(portfolio.load_portfolio()["holdings"]["memer"]["amount"] > 0, "alert only by default: position kept")
cfg["exit_on_new_red_flag"] = True
json.dump(cfg, open("data/risk.json", "w"))
RESULT["r"] = flags("Liquidity 1,500$", "One wallet holds 45.0%")
force_due(); a = run()
expect(len(a) == 1 and a[0].get("sold"), "exit_on_new_red_flag: new flag -> sold")
expect(portfolio.load_portfolio()["holdings"]["memer"]["amount"] == 0, "position closed")

print("RESULT:", "ALL PASS" if not FAILS else f"FAILS {FAILS}")
sys.exit(1 if FAILS else 0)
