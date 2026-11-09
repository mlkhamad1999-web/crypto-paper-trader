"""
Backtest of EXIT / RISK rules on real price history.

It does not pick tokens. It enters every token at regular time intervals (an
entry with no edge) and replays each rule variant from that entry, so the only
thing that differs between variants is the exit logic: stop-loss, trailing
stop, re-entry, hold-until-break-even, take-profit ladder, time stop.

Data sources (GeckoTerminal, free API, cached 6h in backtest_cache/):
  --dex pumpswap   graduated pump.fun tokens (PumpSwap pools)        [default]
  --dex pump-fun   tokens still on the pump.fun bonding curve ("trenches")
  --source repo    data/price_history.json of this repo (≈ hourly closes)
  --ca <address>   specific tokens (repeatable); their most liquid pool is used

  Pools are the most-traded of the last 24h (the only sort the API offers), so
  tokens that died BEFORE that are missing: absolute results are OPTIMISTIC.
  Tokens that die DURING a simulated trade are counted (time-based horizon,
  dead = -100%). Compare variants with each other, not with zero. For absolute
  numbers, see the forward study of 1,411 Solana launches (arrowalgo.com,
  Oct 2026): buying everything 30 min after launch lost ~62% at 6h on average.

Examples:
  python scripts/backtest.py
  python scripts/backtest.py --dex pump-fun --candle-minutes 5 --horizon-hours 2
  python scripts/backtest.py --horizon-hours 24 --pools 150
  python scripts/backtest.py --ca <CA> --ca <CA>

Simulation model (stake 5 USD per entry):
  - costs per side: --fee-pct (default 2.5%: pump.fun/PumpSwap fee up to 1.25%
    + ~1% trading-terminal fee + slippage) and --fixed-fee-usd per transaction
    (default 0.05: priority fee / tip, which matters a lot on a 5$ trade)
  - stops trigger when a candle's LOW crosses the level and fill at the level,
    or at the candle OPEN if it gapped through (slippage)
  - trailing stop: level = highest high of PREVIOUS candles x (1 - trail%)
  - take-profit rungs fill when a candle's HIGH reaches the target; stops are
    checked first inside a candle (conservative)
  - re-entry: at the next candle's open right after a stop (one time only)
  - hold-until-break-even: exit when a later candle's HIGH reaches the entry
  - time stop: exit at the close once N hours have passed without +X%
  - at the horizon, an open position is marked at the last traded price; it
    counts as DEAD (-100%) if that price is <= 10% of entry or if nothing
    traded in the last 3 hours of the window (you could not have sold)
  - validation: tokens are split in two halves (A/B by address). A rule that
    only wins on one half is noise.
"""

import argparse
import bisect
import hashlib
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_prices import _http_get_json
from utils import PROJECT_ROOT, PRICE_HISTORY_PATH, read_json

GT = "https://api.geckoterminal.com/api/v2"
GT_HEADERS = {"Accept": "application/json;version=20230302"}
CACHE_DIR = os.path.join(PROJECT_ROOT, "backtest_cache")
STAKE = 5.0


def V(key, name, stop=None, trail=None, reentry=False, reentry_exit=None, ladder=(), time_stop=None):
    """stop/trail in % (stop negative, trail positive). reentry_exit:
    "breakeven" (no stop, exit at break-even), "stop" (same stop), or a number
    (hard stop %, else exit at break-even). ladder: ((gain%, fraction of the
    initial size), ...). time_stop: (hours, min gain %)."""
    return {"key": key, "name": name, "stop": stop, "trail": trail, "reentry": reentry,
            "reentry_exit": reentry_exit, "ladder": tuple(ladder), "time_stop": time_stop}


VARIANTS = [
    V("mine", "Tes règles: stop -15, ré-entrée hold jusqu'au BE", stop=-15, reentry=True, reentry_exit="breakeven"),
    V("reentry_stop", "Stop -15, ré-entrée avec même stop", stop=-15, reentry=True, reentry_exit="stop"),
    V("stop15", "Stop -15", stop=-15),
    V("stop20", "Stop -20", stop=-20),
    V("stop30", "Stop -30", stop=-30),
    V("trail15", "Trailing stop 15%", trail=15),
    V("trail20", "Trailing stop 20%", trail=20),
    V("trail30", "Trailing stop 30%", trail=30),
    V("stop15_trail20", "Stop -15 + trailing 20%", stop=-15, trail=20),
    V("stop15_trail30", "Stop -15 + trailing 30%", stop=-15, trail=30),
    V("ladder", "Stop -15, vend 1/3 à +50% et 1/3 à x2, trail 30%", stop=-15, trail=30,
      ladder=((50, 1 / 3), (100, 1 / 3))),
    V("tp50", "Stop -15, vend tout à +50%", stop=-15, ladder=((50, 1.0),)),
    V("tp100half", "Stop -15, vend 50% à x2", stop=-15, ladder=((100, 0.5),)),
    V("timestop", "Stop -15 + sortie si pas +10% après 1h", stop=-15, time_stop=(1, 10)),
    V("trail20_time", "Trailing 20% + sortie si pas +10% après 1h", trail=20, time_stop=(1, 10)),
    V("hold", "Aucun stop (hold jusqu'à l'horizon)"),
]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _gt_get(path: str) -> dict:
    data = _http_get_json(GT + path, extra_headers=GT_HEADERS)
    time.sleep(2.2)  # free tier: 30 calls/min
    return data


def _cached(name: str, loader):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 3600:
        with open(path) as f:
            return json.load(f)
    data = loader()
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def gecko_pool_candles(pool: str, minutes: int) -> list:
    """OHLC candles oldest-first: [(ts, o, h, l, c), ...]. GeckoTerminal skips
    intervals with no trades, so gaps in time are real (nobody traded)."""
    tf, agg = ("minute", minutes) if minutes < 60 else ("hour", minutes // 60)

    def load():
        d = _gt_get(f"/networks/solana/pools/{pool}/ohlcv/{tf}?aggregate={agg}&limit=1000&currency=usd&token=base")
        return d["data"]["attributes"]["ohlcv_list"]

    rows = _cached(f"ohlcv_{pool}_{minutes}", load)
    return [(r[0], r[1], r[2], r[3], r[4]) for r in sorted(rows) if r[4] and r[4] > 0]


def sanitize(candles: list, max_wick: float = 3.0) -> list:
    """Illiquid pools print absurd wicks (one tiny trade into an empty pool).
    Nobody can fill there, so cap open/high/low within `max_wick`x of the body
    and of the previous close."""
    out = []
    prev_close = None
    for ts, o, h, l, c in candles:
        ref = prev_close if prev_close else c
        o = min(max(o, ref / max_wick), ref * max_wick)
        body_hi, body_lo = max(o, c), min(o, c)
        h = min(max(h, body_hi), body_hi * max_wick)
        l = max(min(l, body_lo), body_lo / max_wick)
        out.append((ts, o, h, l, c))
        prev_close = c
    return out


def is_erratic(candles: list) -> bool:
    """Wash-traded / spam pools: many candles with huge high/low spreads."""
    wild = sum(1 for _, o, h, l, c in candles if l > 0 and h / l > 3)
    return wild > 0.05 * len(candles)


def gecko_sample(dex: str, n_pools: int, minutes: int, min_liquidity: float) -> dict:
    pools, page, skipped_liq = [], 1, 0
    while len(pools) < n_pools and page <= 10:
        d = _cached(f"{dex}_pools_p{page}",
                    lambda: _gt_get(f"/networks/solana/dexes/{dex}/pools?page={page}&sort=h24_tx_count_desc"))
        for p in d.get("data", []):
            a = p["attributes"]
            if float(a.get("reserve_in_usd") or 0) < min_liquidity:
                skipped_liq += 1
                continue
            pools.append((a["name"].split(" / ")[0], a["address"]))
        page += 1
    series, erratic = {}, []
    todo = pools[:n_pools]
    for i, (name, addr) in enumerate(todo):
        try:
            c = gecko_pool_candles(addr, minutes)
        except Exception as e:
            print(f"  [skip] {name}: {e}")
            continue
        print(f"  {i + 1}/{len(todo)} {name}: {len(c)} candles      ", end="\r", flush=True)
        if len(c) < 2:
            continue
        if is_erratic(c):
            erratic.append(name)
            continue
        series[f"{name} ({addr[:6]})"] = (addr, sanitize(c))
    print()
    print(f"  {len(series)} pools kept; skipped {skipped_liq} under {min_liquidity:,.0f}$ liquidity "
          f"and {len(erratic)} erratic/wash pools")
    return series


def gecko_for_ca(ca: str, minutes: int) -> tuple:
    d = _cached(f"token_pools_{ca}", lambda: _gt_get(f"/networks/solana/tokens/{ca}/pools?page=1"))
    pools = d.get("data", [])
    if not pools:
        raise RuntimeError(f"no pool for {ca}")
    best = max(pools, key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0))
    a = best["attributes"]
    return a["name"].split(" / ")[0], a["address"], sanitize(gecko_pool_candles(a["address"], minutes))


def repo_series() -> dict:
    hist = read_json(PRICE_HISTORY_PATH, [])
    series = {}
    for snap in hist:
        for tid, price in snap.get("prices", {}).items():
            if price:
                series.setdefault(tid, (tid, []))[1].append((snap["epoch"], price, price, price, price))
    return series


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate(candles: list, start: int, end: int, v: dict, fee: float,
             fixed_fee: float = 0.0, horizon_end_ts: float = None) -> dict:
    """Replay one variant from an entry at candles[start].open, up to (not
    including) candles[end]. Returns P&L in USD and flags."""
    st = {"cash": 0.0, "invested": 0.0, "pos": None}
    used_reentry = False

    def buy(idx, mode):
        price = candles[idx][1]
        qty = max(STAKE * (1 - fee) - fixed_fee, 0) / price
        st["cash"] -= STAKE
        st["invested"] += STAKE
        st["pos"] = {"qty": qty, "qty0": qty, "entry": price, "peak": price, "mode": mode,
                     "opened": idx, "ts": candles[idx][0], "rungs": set()}

    def sell(price, q=None):
        pos = st["pos"]
        q = pos["qty"] if q is None else min(q, pos["qty"])
        st["cash"] += q * price * (1 - fee) - fixed_fee
        pos["qty"] -= q
        if pos["qty"] <= pos["qty0"] * 1e-9:
            st["pos"] = None

    i = start
    buy(start, "first")
    while i < end:
        ts, o, h, l, c = candles[i]
        pos = st["pos"]
        if pos is not None:
            e = pos["entry"]
            if pos["mode"] == "reentry" and v["reentry_exit"] != "stop":
                hard = v["reentry_exit"] if isinstance(v["reentry_exit"], (int, float)) else None
                if hard is not None and l <= e * (1 + hard / 100):
                    sell(min(o, e * (1 + hard / 100)))
                elif i > pos["opened"] and h >= e:
                    sell(max(o, e))  # back at break-even (not on the entry candle itself)
            else:
                level = None
                if v["stop"] is not None:
                    level = e * (1 + v["stop"] / 100)
                if v["trail"]:
                    t = pos["peak"] * (1 - v["trail"] / 100)
                    level = t if level is None else max(level, t)
                if level is not None and l <= level:
                    sell(min(o, level))
                    if v["reentry"] and not used_reentry and i + 1 < end:
                        used_reentry = True
                        buy(i + 1, "reentry")
                        i += 1
                        continue
                else:
                    for k, (gain, frac) in enumerate(v["ladder"]):
                        p = st["pos"]
                        if p is not None and k not in p["rungs"] and h >= e * (1 + gain / 100):
                            p["rungs"].add(k)
                            sell(max(o, e * (1 + gain / 100)), frac * p["qty0"])
                    p = st["pos"]
                    if p is not None and v["time_stop"]:
                        hours, min_gain = v["time_stop"]
                        if ts - p["ts"] >= hours * 3600 and c < e * (1 + min_gain / 100):
                            sell(c)
            if st["pos"] is not None:
                st["pos"]["peak"] = max(st["pos"]["peak"], h)
        i += 1

    stuck = dead = False
    pos = st["pos"]
    if pos is not None:
        last_ts, last = candles[end - 1][0], candles[end - 1][4]
        no_recent_trades = horizon_end_ts is not None and horizon_end_ts - last_ts > 3 * 3600 \
            and horizon_end_ts - candles[start][0] >= 6 * 3600
        if last <= 0.1 * pos["entry"] or no_recent_trades:
            dead = True
            st["pos"] = None  # worth nothing: could not be sold
        else:
            stuck = last < pos["entry"]
            sell(last)
    return {"pnl": st["cash"], "invested": st["invested"], "stuck": stuck, "dead": dead,
            "reentered": used_reentry}


def half_of(addr: str) -> str:
    return "A" if int(hashlib.sha1(addr.encode()).hexdigest(), 16) % 2 == 0 else "B"


def run(series: dict, horizon_s: float, step_s: float, fee: float, fixed_fee: float, variants=None):
    variants = variants or VARIANTS
    now_ts = max(c[-1][0] for _, c in series.values())
    results = {v["key"]: [] for v in variants}
    n_entries, tokens_used = 0, set()
    for name, (addr, candles) in series.items():
        ts_list = [x[0] for x in candles]
        t = candles[0][0]
        half = half_of(addr)
        while t + horizon_s <= now_ts:
            s = bisect.bisect_left(ts_list, t)
            e = bisect.bisect_left(ts_list, t + horizon_s)
            # need a trade near the entry time to be able to buy
            if s < e and ts_list[s] - t < step_s:
                n_entries += 1
                tokens_used.add(name)
                for v in variants:
                    r = simulate(candles, s, e, v, fee, fixed_fee, t + horizon_s)
                    r["half"] = half
                    results[v["key"]].append(r)
            t += step_s
    rows = []
    for v in variants:
        r = results[v["key"]]
        if not r:
            continue
        rows.append({"key": v["key"], "variant": v["name"], **stats([x["pnl"] for x in r]),
                     "dead_pct": sum(x["dead"] for x in r) / len(r) * 100,
                     "stuck_pct": sum(x["stuck"] for x in r) / len(r) * 100,
                     "mean_A": stats([x["pnl"] for x in r if x["half"] == "A"])["mean"],
                     "mean_B": stats([x["pnl"] for x in r if x["half"] == "B"])["mean"]})
    return rows, n_entries, len(tokens_used)


def stats(pnls: list) -> dict:
    if not pnls:
        return {"n": 0, "mean": float("nan"), "median": float("nan"), "win": float("nan"),
                "p10": float("nan"), "worst": float("nan")}
    pnls = sorted(pnls)
    cut = len(pnls) // 50  # trimmed mean: drop best and worst 2%
    trimmed = pnls[cut:len(pnls) - cut] or pnls
    return {"n": len(pnls), "mean": statistics.mean(trimmed), "median": statistics.median(pnls),
            "win": sum(p > 0 for p in pnls) / len(pnls) * 100, "p10": pnls[len(pnls) // 10],
            "worst": pnls[0]}


def print_table(rows: list, title: str):
    print(f"\n{title}")
    print(f"{'Variante':<50} {'Moy.$':>7} {'Méd.$':>7} {'Gagn%':>6} {'P10$':>6} {'Pire$':>6} "
          f"{'Mort%':>6} {'Coincé%':>7} {'Moy.A':>7} {'Moy.B':>7}")
    print("-" * 120)
    for r in sorted(rows, key=lambda r: r["mean"], reverse=True):
        print(f"{r['variant']:<50} {r['mean']:>+7.3f} {r['median']:>+7.3f} {r['win']:>6.1f} {r['p10']:>+6.2f} "
              f"{r['worst']:>+6.2f} {r['dead_pct']:>6.1f} {r['stuck_pct']:>7.1f} {r['mean_A']:>+7.3f} {r['mean_B']:>+7.3f}")
    print("\nMoy. = P&L moyen par setup (5$ par entrée) sans les 2% extrêmes. Méd. = médian. "
          "P10 = 10% des setups font pire.")
    print("Mort% = position invendable / -90% à l'horizon. Coincé% = encore ouverte en perte.")
    print("Moy.A / Moy.B = la même moyenne sur deux moitiés de tokens séparées: une règle robuste gagne sur les deux.")


def main():
    ap = argparse.ArgumentParser(description="Backtest exit/risk rule variants on real prices")
    ap.add_argument("--source", choices=["gecko", "repo"], default="gecko")
    ap.add_argument("--dex", choices=["pumpswap", "pump-fun"], default="pumpswap")
    ap.add_argument("--ca", action="append", default=[], help="Token contract address (repeatable)")
    ap.add_argument("--pools", type=int, default=120, help="Number of pools to sample (gecko)")
    ap.add_argument("--candle-minutes", type=int, default=15, choices=[1, 5, 15, 60, 240])
    ap.add_argument("--horizon-hours", type=float, default=6, help="How long each trade can last")
    ap.add_argument("--entry-every-hours", type=float, default=1, help="Spacing between simulated entries")
    ap.add_argument("--fee-pct", type=float, default=2.5, help="Cost per side (fees + slippage), %%")
    ap.add_argument("--fixed-fee-usd", type=float, default=0.05, help="Fixed cost per transaction (priority fee/tip)")
    ap.add_argument("--min-liquidity", type=float, default=0, help="Skip pools with less USD liquidity now")
    ap.add_argument("--json", help="Also write the results to this JSON file")
    args = ap.parse_args()

    if args.ca:
        series = {}
        for ca in args.ca:
            name, addr, c = gecko_for_ca(ca, args.candle_minutes)
            series[name] = (addr, c)
    elif args.source == "repo":
        series = repo_series()
    else:
        print(f"Fetching {args.pools} {args.dex} pools from GeckoTerminal (cached 6h)...")
        series = gecko_sample(args.dex, args.pools, args.candle_minutes, args.min_liquidity)

    if not series:
        print("No data.")
        sys.exit(1)
    rows, n, n_tokens = run(series, args.horizon_hours * 3600, args.entry_every_hours * 3600,
                            args.fee_pct / 100, args.fixed_fee_usd)
    if not rows:
        print("Not enough history for this horizon. Try a shorter --horizon-hours.")
        sys.exit(1)
    print_table(rows, f"{n_tokens} tokens, {n} entrées simulées, horizon {args.horizon_hours:g}h, "
                      f"frais {args.fee_pct}%/côté + {args.fixed_fee_usd}$/tx")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"tokens": n_tokens, "entries": n, "args": vars(args), "results": rows}, f, indent=2)


if __name__ == "__main__":
    main()
