"""
Pre-trade safety check for a Solana token (anti-scam, not a buy signal).

    python scripts/trade.py check <CA>

Data: RugCheck public report (api.rugcheck.xyz, no key needed) + GeckoTerminal
pools. Thresholds follow the rug-detection / token-analysis checklists of
SanctifiedOps/solana-skills:

  RED  (major red flag -> refused by your rules):
       mint authority active, freeze authority active, token flagged rugged,
       transfer fee / tax, liquidity < 5k$, top-10 holders > 60% (excluding
       AMM/LP accounts), one wallet > 30%, creator holds > 10%, insiders hold
       > 10%, any RugCheck "danger" risk
  WARN liquidity < 10k$, top-10 > 40%, one wallet > 10%, creator > 5%,
       insider networks detected, main LP < 50% locked/burned, pool < 24h old,
       creator launched many tokens, any RugCheck "warn" risk

Passing the check only means none of these known patterns showed up. It says
nothing about whether the price will go up. Soft rugs (team dumps over days)
are not detectable this way.
"""

from datetime import datetime, timezone

from fetch_prices import _http_get_json

RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
GT_POOLS = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1"


def fetch_rugcheck(mint: str) -> dict:
    return _http_get_json(RUGCHECK.format(mint=mint), timeout=30)


def fetch_main_pool(mint: str):
    try:
        d = _http_get_json(GT_POOLS.format(mint=mint), extra_headers={"Accept": "application/json;version=20230302"})
    except Exception:
        return None
    pools = d.get("data") or []
    if not pools:
        return None
    return max(pools, key=lambda p: float(p["attributes"].get("reserve_in_usd") or 0))["attributes"]


def analyze(rc: dict, pool: dict = None) -> dict:
    flags = []  # (level, label, detail)

    def add(level, label, detail=""):
        flags.append((level, label, detail))

    known = rc.get("knownAccounts") or {}
    supply = (rc.get("token") or {}).get("supply") or 0

    # 1. Authorities
    if rc.get("mintAuthority"):
        add("RED", "Mint authority ACTIVE", "the creator can print unlimited tokens")
    else:
        add("OK", "Mint authority revoked")
    if rc.get("freezeAuthority"):
        add("RED", "Freeze authority ACTIVE", "your tokens can be frozen (unsellable)")
    else:
        add("OK", "Freeze authority revoked")
    if rc.get("rugged"):
        add("RED", "Flagged as RUGGED by RugCheck")
    fee = (rc.get("transferFee") or {}).get("pct") or 0
    if fee > 0:
        add("RED", f"Transfer fee {fee}%", "hidden tax on every transfer")

    # 2. Liquidity and LP. On the bonding curve RugCheck reports ~0$ liquidity
    # (the reserve is the curve itself), so take the larger of both sources.
    markets_all = rc.get("markets") or []
    # (side pools, e.g. a small Meteora pool, can exist next to the curve)
    on_curve = any(m.get("marketType") == "pump_fun" for m in markets_all)
    gt_liq = float(pool["reserve_in_usd"]) if pool and pool.get("reserve_in_usd") else 0
    liq = max(rc.get("totalMarketLiquidity") or 0, gt_liq)
    if liq < 5000:
        add("RED", f"Liquidity {liq:,.0f}$", "< 5k$: extreme slippage, trivial to manipulate")
    elif liq < 10000:
        add("WARN", f"Liquidity {liq:,.0f}$", "< 10k$")
    else:
        add("OK", f"Liquidity {liq:,.0f}$")
    markets = [m for m in markets_all if m.get("lp")]
    if on_curve:
        add("INFO", "Still on the pump.fun bonding curve", "no LP yet; graduation pending")
    elif markets:
        main = max(markets, key=lambda m: (m["lp"].get("lpLockedUSD") or 0) + (m["lp"].get("quoteUSD") or 0))
        locked = main["lp"].get("lpLockedPct") or 0
        mtype = main.get("marketType", "?")
        if locked < 50:
            add("WARN", f"Main LP only {locked:.0f}% locked/burned ({mtype})", "liquidity can be pulled")
        else:
            add("OK", f"Main LP {locked:.0f}% locked/burned ({mtype})")

    # 3. Holders (excluding AMM / LP / known program accounts)
    holders = [h for h in (rc.get("topHolders") or []) if (known.get(h.get("owner")) or {}).get("type") != "AMM"
               and (known.get(h.get("address")) or {}).get("type") != "AMM"]
    top10 = sum(h.get("pct", 0) for h in holders[:10])
    biggest = holders[0].get("pct", 0) if holders else 0
    if top10 > 60:
        add("RED", f"Top 10 holders {top10:.1f}%", "> 60%: coordinated supply, dump risk")
    elif top10 > 40:
        add("WARN", f"Top 10 holders {top10:.1f}%", "> 40%")
    else:
        add("OK", f"Top 10 holders {top10:.1f}%")
    if biggest > 30:
        add("RED", f"One wallet holds {biggest:.1f}%")
    elif biggest > 10:
        add("WARN", f"One wallet holds {biggest:.1f}%")
    insider_pct = sum(h.get("pct", 0) for h in holders if h.get("insider"))
    if insider_pct > 10:
        add("RED", f"Insiders hold {insider_pct:.1f}% (top holders)")
    n_ins = rc.get("graphInsidersDetected") or 0
    if n_ins:
        add("WARN", f"{n_ins} insider wallets detected", "linked wallets (same funding / transfers)")

    # 4. Creator
    if supply and rc.get("creatorBalance"):
        cpct = rc["creatorBalance"] / supply * 100
        if cpct > 10:
            add("RED", f"Creator holds {cpct:.1f}%")
        elif cpct > 5:
            add("WARN", f"Creator holds {cpct:.1f}%")
    ctoks = rc.get("creatorTokens") or []
    if len(ctoks) >= 5:
        add("WARN", f"Creator launched {len(ctoks)} other tokens", "serial launcher")

    # 5. Age
    if pool and pool.get("pool_created_at"):
        created = datetime.fromisoformat(pool["pool_created_at"].replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        if age_h < 24:
            add("WARN", f"Pool age {age_h:.1f}h", "< 24h: maximum risk window")
        else:
            add("INFO", f"Pool age {age_h / 24:.1f} days")

    # 6. RugCheck's own risk list
    for r in rc.get("risks") or []:
        lvl = {"danger": "RED", "warn": "WARN"}.get(r.get("level"), "INFO")
        if on_curve and "liquidity" in (r.get("name") or "").lower():
            lvl = "INFO"  # already judged above with the curve reserve
        add(lvl, f"RugCheck: {r.get('name')}", str(r.get("value") or r.get("description") or ""))

    reds = [f for f in flags if f[0] == "RED"]
    warns = [f for f in flags if f[0] == "WARN"]
    return {"flags": flags, "red": len(reds), "warn": len(warns),
            "score_normalised": rc.get("score_normalised"), "holders": rc.get("totalHolders"),
            "liquidity": liq, "top10": top10}


def check(mint: str) -> dict:
    rc = fetch_rugcheck(mint)
    pool = fetch_main_pool(mint)
    result = analyze(rc, pool)
    result["symbol"] = (rc.get("tokenMeta") or {}).get("symbol") or (pool or {}).get("name", "?")
    result["pool"] = pool
    return result


def print_report(mint: str, r: dict) -> None:
    icons = {"RED": "✗ RED ", "WARN": "! WARN", "OK": "✓ ok  ", "INFO": "· info"}
    print(f"Safety check {r['symbol']} — {mint}")
    print(f"  holders {r['holders']}, liquidity {r['liquidity']:,.0f}$, RugCheck score {r['score_normalised']}/100 (higher = riskier)")
    if r.get("pool"):
        p = r["pool"]
        tx = (p.get("transactions") or {}).get("h24") or {}
        print(f"  24h volume {float((p.get('volume_usd') or {}).get('h24') or 0):,.0f}$, "
              f"buyers {tx.get('buyers', '?')} / sellers {tx.get('sellers', '?')}")
    print()
    order = {"RED": 0, "WARN": 1, "OK": 2, "INFO": 3}
    for level, label, detail in sorted(r["flags"], key=lambda f: order[f[0]]):
        print(f"  {icons[level]}  {label}" + (f" — {detail}" if detail else ""))
    print()
    if r["red"]:
        print(f"  VERDICT: {r['red']} red flag(s) majeur(s) → REFUS selon tes règles.")
    elif r["warn"]:
        print(f"  VERDICT: aucun red flag majeur, {r['warn']} alerte(s). Ce n'est pas un signal d'achat.")
    else:
        print("  VERDICT: aucun red flag connu détecté. Ce n'est pas un signal d'achat.")
