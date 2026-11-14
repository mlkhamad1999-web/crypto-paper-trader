"""Unit tests of the backtest simulator on hand-made candles (offline)."""
import os, sys

ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from backtest import simulate, VARIANTS, run  # noqa: E402

K = {v["key"]: v for v in VARIANTS}
C = lambda rows, step=900: [(i * step, *r) for i, r in enumerate(rows)]
FAILS = []


def chk(name, got, want):
    ok = abs(got - want) < 1e-9
    print(("  PASS " if ok else "  FAIL ") + f"{name}: {got:+.4f} (want {want:+.4f})")
    if not ok:
        FAILS.append(name)


def flag(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        FAILS.append(name)


# candles: (open, high, low, close); no fees unless stated
c = C([(1, 1, 1, 1), (1, 1, 0.8, 0.82), (0.80, 0.81, 0.5, 0.6), (0.6, 0.85, 0.6, 0.85)])
chk("hold-to-BE re-entry exits at break-even", simulate(c, 0, 4, K["mine"], 0)["pnl"], -0.75)
chk("stop -15 only", simulate(c, 0, 4, K["stop15"], 0)["pnl"], -0.75)
c2 = C([(1, 1, 1, 1), (0.5, 0.5, 0.4, 0.45), (0.45, 0.45, 0.45, 0.45)])
chk("gap fills at the open", simulate(c2, 0, 3, K["stop15"], 0)["pnl"], -2.5)
c3 = C([(1, 1, 1, 1), (1, 1, 0.8, 0.8), (0.8, 0.8, 0.3, 0.3), (0.3, 0.3, 0.2, 0.2)])
r = simulate(c3, 0, 4, K["mine"], 0)
chk("stuck re-entry marked at last price", r["pnl"], -0.75 + 5 * 0.2 / 0.8 - 5)
flag("stuck flag", r["stuck"])
chk("re-entry with the same stop", simulate(c3, 0, 4, K["reentry_stop"], 0)["pnl"], -0.75 + 5 * 0.68 / 0.8 - 5)
c4 = C([(1, 1, 1, 1), (1, 2.0, 1, 1.9), (1.9, 1.9, 1.5, 1.5)])
chk("trailing 20% locks +60%", simulate(c4, 0, 3, K["trail20"], 0)["pnl"], 5 * 1.6 - 5)
c5 = C([(1, 1, 1, 1), (1, 2.2, 1, 2.1), (2.1, 2.1, 1.0, 1.1)])
chk("ladder 1/3@1.5 + 1/3@2 + rest trailing 1.54", simulate(c5, 0, 3, K["ladder"], 0)["pnl"],
    5 / 3 * 1.5 + 5 / 3 * 2 + 5 / 3 * 1.54 - 5)
c6 = C([(1, 1, 1, 1), (1, 1.05, 0.95, 1.0), (1, 1.05, 0.95, 1.0), (1, 1.05, 0.95, 1.0),
        (1, 1.05, 0.95, 0.98), (0.98, 1.0, 0.9, 0.95)])
chk("time stop sells at the close after 1h", simulate(c6, 0, 6, K["timestop"], 0)["pnl"], 5 * 0.98 - 5)
c7 = [(0, 1, 1, 1, 1), (900, 1, 1, 0.9, 0.95)]
r = simulate(c7, 0, 2, K["hold"], 0, 0, 6 * 3600)
chk("token with no trades for 3h = dead (-100%)", r["pnl"], -5)
flag("dead flag", r["dead"])
flag("price -95% = dead", simulate(C([(1, 1, 1, 1), (0.05, 0.05, 0.05, 0.05)]), 0, 2, K["hold"], 0)["dead"])
chk("fees 2.5%/side + 0.05$/tx", simulate(C([(1, 1, 1, 1), (1, 1, 1, 1)]), 0, 2, K["hold"], 0.025, 0.05)["pnl"],
    (5 * 0.975 - 0.05) * 0.975 - 0.05 - 5)
series = {"alive": ("aa", C([(1, 1, 1, 1)] * 40)), "dies": ("bb", [(0, 1, 1, 1, 1), (900, 1, 1, 0.5, 0.5)])}
rows, n, nt = run(series, 6 * 3600, 3600, 0, 0, [K["hold"]])
flag(f"a token that stops trading is still simulated (tokens={nt})", nt == 2)

print("RESULT:", "ALL PASS" if not FAILS else f"FAILS {FAILS}")
sys.exit(1 if FAILS else 0)
