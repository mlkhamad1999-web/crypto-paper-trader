# Crypto Paper Trader — zAI vs gAi

A no-KYC, no-server paper trading tracker for crypto tokens, with **live
browser-side price fetching** so the dashboard always shows current prices
without needing a server cron.

Built as an experiment: track $1 paper buys of 6 hand-picked tokens
recommended by 2 different AIs (zAI and gAi), and see who makes better bets.

> ⚠️ **Not financial advice.** Paper trading only. Real crypto is risky — never
> invest more than you can afford to lose.

## 🥊 The duel

| AI | Tokens | Strategy |
|---|---|---|
| **zAI** | TENDIES, CASHCAT, FOX (Robinhood Chain), PUMP (Solana) | Memecoin-focused — early bets on the new Robinhood Chain narrative + Solana's pump.fun |
| **gAi** | LISTA, XVS (both BNB chain) | DeFi-focused — established BNB chain lending protocols with real revenue |

Each token was bought for **$1 of paper money** at the live market price when
the portfolio was initialized. zAI deployed $4, gAi deployed $2.

## 🌐 Live dashboard

Once GitHub Pages is enabled (it is, by default), the dashboard is at:

```
https://sonamother.github.io/crypto-paper-trader/
```

It shows:
- **Hero card** — total portfolio value + P&L (live, refreshing every 60s)
- **AI Leaderboard** — ranked list of which AI is winning
- **Per-AI breakdown** — side-by-side cards for zAI's basket vs gAi's basket
- **Portfolio value over time** — line chart of historical value (from hourly snapshots)
- **Holdings grid** — 6 token cards with live prices, P&L, 24h change, AI tag
- **Trade history** — full paper trade log
- **Live price source** — shows "CoinGecko (live)" or "Cached (rate-limited)" so you know if data is fresh

## 🔄 How live updates work (two layers)

The dashboard has **two independent price refresh mechanisms**:

### Layer 1: Browser-side live prices (every 60s)
When you have the dashboard open, JavaScript fetches live prices directly
from CoinGecko's public API every 60 seconds. No server needed. This is what
makes the dashboard feel "live" — prices update on screen while you watch.

### Layer 2: Server-side hourly snapshots (via GitHub Actions cron)
A Python script runs every hour (via GitHub Actions), fetches prices, and
commits a new snapshot to `data/price_history.json`. This builds the
historical chart data over time. Every snapshot is a git commit, so the full
portfolio history is preserved.

If the cron isn't activated yet (see `SETUP_WORKFLOW.md`), the dashboard
still works perfectly — you just won't get new chart history points.
Browser-side live prices keep the dashboard current.

## 🛠 Tech stack

- **Backend** (cron only): Python 3, standard library only (urllib, json)
- **Frontend**: Vanilla HTML/CSS/JS (no frameworks, no build step)
- **Charts**: Chart.js via CDN
- **Hosting**: GitHub Pages (static, free, automatic)
- **Auto-updates**: GitHub Actions cron (hourly)
- **Price APIs**: CoinGecko (primary, CORS-enabled), GeckoTerminal (fallback)

No Tailwind CDN. No build step. No npm install. Just open the HTML and it
works.

## 🚀 How to run locally

```bash
# 1. Clone
git clone https://github.com/SonaMother/crypto-paper-trader.git
cd crypto-paper-trader

# 2. Refresh prices server-side (writes to data/portfolio.json)
python scripts/update_portfolio.py

# 3. Show portfolio status
python scripts/trade.py status

# 4. Serve the dashboard locally
python -m http.server 8000
# Open http://localhost:8000
```

## 💼 Manual paper trades

```bash
# Buy $5 of TENDIES at current market price (tagged as zAI)
python scripts/trade.py buy tendies --usd 5

# Buy 1000 FOX at current market price
python scripts/trade.py buy fox --amount 1000

# Sell 50% of PUMP holdings
python scripts/trade.py sell pump --pct 50

# Show current portfolio status
python scripts/trade.py status
```

After running a manual trade, commit and push the updated `data/` files:

```bash
git add data/
git commit -m "manual: <describe your trade>"
git push
```

## ➕ Adding a new AI's picks

To add a new AI's recommendations:

1. Edit `scripts/config.py` — add new token entries with the `recommended_by`
   field set to the new AI's name (e.g. `"kAi"`).
2. Edit `config.js` (the browser-side config) — mirror the new tokens and
   the new AI's style in `aiStyles`.
3. Run `python scripts/init_portfolio.py` — it will detect the new tokens
   and buy $1 of each at the current price (smart init — only buys tokens
   that don't have a position yet).
4. Push to GitHub: `git add -A && git commit -m "add kAi picks" && git push`

## ➕ Adding a single new token to an existing AI

Same flow as above, but you only add one token entry. The smart init script
will buy just that one new token.

## 🔄 Re-initializing the portfolio (start over)

```bash
python scripts/init_portfolio.py --force
```

Wipes all trades, holdings, and price history. Rebuys $1 of every token in
`config.py` at current prices.

## 📁 Project structure

```
.
├── README.md
├── SETUP_WORKFLOW.md              # how to activate the GitHub Actions cron
├── requirements.txt               # (empty - we use stdlib only)
├── .gitignore
├── index.html                     # dashboard (served by GitHub Pages)
├── style.css                      # custom dark theme (no Tailwind CDN)
├── app.js                         # dashboard logic + live price fetching
├── config.js                      # browser-side token config
├── .github/
│   └── workflows/
│       └── update.yml             # hourly cron job
├── scripts/
│   ├── config.py                  # token definitions (with recommended_by)
│   ├── fetch_prices.py            # CoinGecko + GeckoTerminal client
│   ├── portfolio.py               # portfolio data layer + per-AI summary
│   ├── init_portfolio.py          # smart init (only buys missing tokens)
│   ├── update_portfolio.py        # refresh prices + mark-to-market
│   ├── trade.py                   # manual trade CLI
│   ├── utils.py                   # shared helpers
│   ├── migrate_add_recommended_by.py        # one-shot migration
│   └── migrate_add_recommended_by_trades.py # one-shot migration
└── data/
    ├── portfolio.json             # current holdings + cash
    ├── trades.json                # trade history (with AI tag per trade)
    └── price_history.json         # time-series of portfolio value
```

## 📊 Data schemas

### `data/portfolio.json`
```json
{
  "created_at": "2026-07-26T16:46:10Z",
  "cash_usd": 0.0,
  "holdings": {
    "tendies": {
      "token_id": "tendies",
      "ticker": "TENDIES",
      "name": "TENDIES",
      "chain": "Robinhood",
      "color": "#f59e0b",
      "recommended_by": "zAI",
      "amount": 64.0878,
      "cost_basis_usd": 1.0,
      "last_price": 0.01560,
      "last_price_updated_at": "..."
    }
  },
  "last_updated_at": "..."
}
```

### `data/trades.json`
```json
[
  {
    "id": "uuid",
    "ts": "2026-07-26T16:46:15Z",
    "epoch": 1785084375,
    "action": "BUY",
    "token_id": "tendies",
    "ticker": "TENDIES",
    "chain": "Robinhood",
    "recommended_by": "zAI",
    "amount": 64.0878,
    "price_usd": 0.01560,
    "value_usd": 1.0,
    "note": "Initial paper buy: $1.00 of TENDIES (by zAI)"
  }
]
```

### `data/price_history.json`
```json
[
  {
    "ts": "2026-07-26T16:47:06Z",
    "epoch": 1785084426,
    "prices": { "tendies": 0.01560, "cashcat": 0.04916, ... },
    "portfolio_value_usd": 6.00
  }
]
```

## 🔒 Privacy & security notes

- No real funds are ever moved. This is 100% paper trading.
- The dashboard makes client-side API calls to CoinGecko from the user's
  browser — no API keys are exposed.
- The Python scripts run on GitHub Actions servers (or your local machine)
  and also use no API keys.
- **Important**: If you fork this repo, regenerate your own GitHub PAT —
  don't reuse the one in the commit history.

## 📜 License

MIT — do whatever you want with this code.

## ⚠️ Disclaimer

This is a research project for tracking paper trades and comparing AI
recommendations. It is not financial advice. The tokens tracked here are
extremely risky (memecoins on a 3-week-old blockchain, micro-cap moonshots,
volatile DeFi tokens) and most will probably lose value. The purpose is to
learn and observe, not to make real money.

## 🎯 Hand-picked Solana tokens, safety check & risk rules

```bash
# Anti-scam check (RugCheck + GeckoTerminal): authorities, tax, liquidity,
# holder concentration, creator, insiders, LP lock, age. Not a buy signal.
python scripts/trade.py check <CA>

# Track a token by contract address (runs the check; refuses major red flags)
python scripts/trade.py add-token <CA>

# Paper buy / sell with the risk rules of data/risk.json enforced
python scripts/trade.py buy <id> --usd 5            # --exceptional allows up to 8
python scripts/trade.py sell <id> --pct 100
python scripts/trade.py risk                        # daily P&L, capital, stops, re-entries
python scripts/trade.py journal                     # your results vs random entries: do you have an edge?
```

Risk rules (`data/risk.json`, USD): capital max 50, daily realized loss max 10
(Europe/Brussels day), position 5 (8 exceptional), stop -15%, trailing stop 30%
from the peak, one re-entry after a stop (protected by the same stop).

## 🧪 Backtesting exit rules

```bash
python scripts/backtest.py                                   # PumpSwap, 6h horizon
python scripts/backtest.py --dex pump-fun --candle-minutes 5 --horizon-hours 2
python scripts/backtest.py --ca <CA> --ca <CA>
```

Entries are taken at regular intervals (no picking skill), so only the exit
rules differ. Costs: 2.5% per side + 0.05$ per transaction. Tokens are split in
two halves: a rule that only wins on one half is noise. Results are optimistic
(pools that died before the last 24h are missing), so compare variants with each
other. Result in Nov 2026: every variant lost money at 2h–6h horizons; stop -15 +
trailing 30% beat stop -15 alone in 2 of 3 datasets; holding a re-entry until
break-even never did.

Open hand-picked positions are re-checked for scams every 6h by the hourly
update (`safety_recheck_hours`). A NEW major red flag (liquidity pulled, a wallet
suddenly holding 40%...) is logged as a `safety_alert` in the dashboard activity
log; set `"exit_on_new_red_flag": true` in `data/risk.json` to also sell.

## 🖱 Trading from the browser (no Python needed)

GitHub → **Actions** tab → **Paper trade** → **Run workflow**, then pick:

| command | target | other fields |
|---|---|---|
| `check` | contract address | — |
| `add-token` | contract address | `force` = track despite red flags |
| `buy` | token id (e.g. `wif`) | `usd`, `exceptional` |
| `sell` | token id | `pct`, `force` |
| `risk`, `journal`, `status` | — | — |

The output appears in the run's **Summary**. Trades are committed to `data/`
and show up on the dashboard ("My trades — risk rules" panel). Inputs are
validated by `scripts/run_action.py` and never pasted into a shell command.

## ✅ Tests

```bash
python tests/run_all.py
```

Offline suite (backtest simulator, full risk-rule scenario, safety re-check),
run on a throwaway copy of the repo so real data is never touched. It also runs
on GitHub (workflow "Tests") on every change to `scripts/` or `tests/`.
