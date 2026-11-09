"""
Token configuration for the paper trading portfolio.

Each token includes a `recommended_by` field tagging which AI recommended it.
We use this to compute per-AI leaderboard stats in addition to per-token P&L.

To add a new token:
  1. Find its CoinGecko coin ID (visit coingecko.com, search the token,
     copy the ID from the URL: e.g. /coins/lista -> ID is "lista")
  2. Add an entry below.
"""

TOKENS = [
    # ---------------------------------------------------------------------------
    # zAI's picks (4 tokens)
    # ---------------------------------------------------------------------------
    {
        "id": "tendies",
        "ticker": "TENDIES",
        "name": "TENDIES",
        "chain": "Robinhood",
        "coingecko_id": "tendies-2",
        "contract": None,  # verify on GeckoTerminal Robinhood chain
        "color": "#f59e0b",
        "recommended_by": "zAI",
        "note": "zAI Pick #1 - Robinhood Chain #2 memecoin, gaining while category bleeds",
    },
    {
        "id": "cashcat",
        "ticker": "CASHCAT",
        "name": "Cash Cat",
        "chain": "Robinhood",
        "coingecko_id": "cash-cat",
        "contract": "0x020bfC650A365f8BB26819deAAbF3E21291018b4",
        "color": "#3b82f6",
        "recommended_by": "zAI",
        "note": "zAI Pick #2 - Robinhood Chain flagship meme, down 70% from ATH, accumulating zone",
    },
    {
        "id": "fox",
        "ticker": "FOX",
        "name": "Robin Hood",
        "chain": "Robinhood",
        "coingecko_id": "robin-hood-2",
        "contract": "0x2103faA9D1762e27a716C61718b3aCf3Ec1F9bf1",
        "color": "#10b981",
        "recommended_by": "zAI",
        "note": "zAI Pick #3 - Micro-cap moonshot on Robinhood Chain, themed after the chain itself",
    },
    {
        "id": "pump",
        "ticker": "PUMP",
        "name": "Pump.fun",
        "chain": "Solana",
        "coingecko_id": "pump-fun",
        "contract": "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",
        "color": "#8b5cf6",
        "recommended_by": "zAI",
        "note": "zAI Pick #4 - Safer play, real revenue, listed on Binance US",
    },

    # ---------------------------------------------------------------------------
    # gAi's picks (2 tokens - both on BNB/BSC chain)
    # ---------------------------------------------------------------------------
    {
        "id": "lista",
        "ticker": "LISTA",
        "name": "Lista DAO",
        "chain": "BSC",
        "coingecko_id": "lista",
        "contract": None,  # BSC contract: 0x0615dbaca2c3ee9a341ee946b3625c688b15a9f9
        "color": "#ec4899",
        "recommended_by": "gAi",
        "note": "gAi Pick #1 - Best high-upside valuation mismatch on BNB chain (per gAi)",
    },
    {
        "id": "xvs",
        "ticker": "XVS",
        "name": "Venus",
        "chain": "BSC",
        "coingecko_id": "venus",
        "contract": None,  # BSC contract: 0xcf6bb5389c92bdda8a3747ddb454cb7a6d2626c3
        "color": "#06b6d4",
        "recommended_by": "gAi",
        "note": "gAi Pick #2 - Established lending protocol at a small valuation (per gAi)",
    },
]

# Initial paper-trading allocation: $1 USD per token
INITIAL_BUY_USD = 1.00

# Cash buffer in paper portfolio (USD) - for tracking uninvested capital
INITIAL_CASH_USD = 0.00

# Per-AI styling (used by dashboard)
AI_STYLES = {
    "zAI": {
        "color": "#8b5cf6",        # purple
        "label": "zAI",
        "description": "Memecoin-focused: Robinhood Chain narrative + Solana pump.fun",
    },
    "gAi": {
        "color": "#06b6d4",        # cyan
        "label": "gAi",
        "description": "DeFi-focused: BNB chain lending protocols with real revenue",
    },
    "human": {
        "color": "#f97316",        # orange
        "label": "human",
        "description": "Hand-picked tokens added by contract address, governed by data/risk.json",
    },
}


# ---------------------------------------------------------------------------
# Custom tokens, added by contract address with:
#     python scripts/trade.py add-token <CA>
# They live in data/custom_tokens.json (not in this file) so the dashboard can
# read them too. Custom tokens are priced via GeckoTerminal, are never bought
# automatically by init_portfolio.py, and are governed by the risk rules in
# data/risk.json (see scripts/risk.py).
# ---------------------------------------------------------------------------

def _load_custom_tokens() -> list:
    from utils import read_json, CUSTOM_TOKENS_PATH
    tokens = read_json(CUSTOM_TOKENS_PATH, [])
    return tokens if isinstance(tokens, list) else []


_builtin_ids = {t["id"] for t in TOKENS}
for _t in _load_custom_tokens():
    if not _t.get("id") or _t["id"] in _builtin_ids:
        continue
    _t.setdefault("coingecko_id", None)
    _t.setdefault("recommended_by", "human")
    _t["custom"] = True
    TOKENS.append(_t)
    _builtin_ids.add(_t["id"])
