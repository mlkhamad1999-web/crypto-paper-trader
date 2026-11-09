/* ==========================================================================
   Client-side token configuration.
   Mirrors scripts/config.py — kept in sync manually (or via the build script).

   The browser uses this to know which tokens to fetch live prices for,
   and how to display them (color, AI tag, etc.).
   ========================================================================== */

window.TOKEN_CONFIG = {
  tokens: [
    { id: "tendies", ticker: "TENDIES", name: "TENDIES", chain: "Robinhood", color: "#f59e0b", recommended_by: "zAI" },
    { id: "cashcat", ticker: "CASHCAT", name: "Cash Cat", chain: "Robinhood", color: "#3b82f6", recommended_by: "zAI" },
    { id: "fox",     ticker: "FOX",     name: "Robin Hood", chain: "Robinhood", color: "#10b981", recommended_by: "zAI" },
    { id: "pump",    ticker: "PUMP",    name: "Pump.fun", chain: "Solana",    color: "#8b5cf6", recommended_by: "zAI" },
    { id: "lista",   ticker: "LISTA",   name: "Lista DAO", chain: "BSC",     color: "#ec4899", recommended_by: "gAi" },
    { id: "xvs",     ticker: "XVS",     name: "Venus",     chain: "BSC",     color: "#06b6d4", recommended_by: "gAi" },
  ],

  // Mapping from internal token id to CoinGecko coin id (for live price API)
  coingeckoIds: {
    tendies: "tendies-2",
    cashcat: "cash-cat",
    fox:     "robin-hood-2",
    pump:    "pump-fun",
    lista:   "lista",
    xvs:     "venus",
  },

  aiStyles: {
    zAI: { color: "#8b5cf6", label: "zAI", description: "Memecoin-focused: Robinhood Chain + Solana pump.fun" },
    gAi: { color: "#06b6d4", label: "gAi", description: "DeFi-focused: BNB chain lending protocols with real revenue" },
    human: { color: "#f97316", label: "human", description: "Hand-picked by contract address, governed by data/risk.json" },
  },

  // GeckoTerminal network slugs, for tokens tracked by contract address
  // (data/custom_tokens.json, loaded by app.js at startup).
  geckoterminalNetworks: {
    Solana: "solana", Robinhood: "robinhood", BSC: "bsc", Base: "base", Ethereum: "eth", Arbitrum: "arbitrum",
  },
};
