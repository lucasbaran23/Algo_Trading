# CMDTY//DESK: Commodities Terminal for Prop Traders

A live, terminal-style dashboard for commodity futures **day trading and swing trading**. It combines
quantitative signals (trend, breakout, mean reversion, carry, seasonality, volatility regime) with
quantamental ones (CFTC COT positioning, inventory supply/demand vs 5-year norms). It adds forecasts,
real-time alerts, and a **prop desk** that tracks evaluation/funded-account rules and blocks trades
that would breach them.

> Working name. Do a trademark search before you pick the commercial brand.

## Quick start

```bash
pip install -r backend/requirements.txt
cp .env.example .env          # optional: DATA_MODE=live and an EIA key
./run.sh                      # open http://localhost:8000
```

Run the tests with `cd backend && python -m pytest -q`.

`DATA_MODE=sim` (the default) runs fully offline on deterministic simulated data, and every panel shows
**SIM** source badges. `DATA_MODE=live` pulls real data and falls back to sim **per source** if a feed fails:

| Data | Source | Key | Notes |
|---|---|---|---|
| COT positioning | CFTC Public Reporting API, Disaggregated Futures-Only | none | weekly (Tue data, Fri release) |
| Inventories | EIA Open Data v2 (crude, gasoline, distillate, nat gas storage) | free | weekly |
| Prices | Yahoo Finance chart API | none | **prototype only**: delayed, not licensed for redistribution |

## Screens (F-keys or the command line)

| Key | Screen | What it shows |
|---|---|---|
| F1 | MONITOR | Watchlist with swing/day composites, COT index and 5-day up-probability. Daily or 1-min candles with pivots, VWAP and a 20-day forecast cone |
| F2 | COT | Managed-money vs commercial net positions, COT index with crowded-long/short bands, cross-market table |
| F3 | SUPPLY/DEMAND | Stocks vs 5-yr same-week average, deviation σ, weekly surprise vs the normal change |
| F4 | FORECAST | 5d/20d targets, 1σ/2σ ranges, P(up), model components and the **walk-forward hit rate** |
| F5 | SIGNAL MATRIX | Every factor × every market as a heat map |
| F6 | PROP DESK | Rule editor and presets, drawdown/daily-loss/target meters, risk sizer, bracket order ticket, positions, trade log |
| F7 | ALERTS | Built-in triggers plus custom rules |
| F8 | HELP | Commands and methodology |

Command line examples: `CL`, `NG COT`, `GC FCST`, `BUY 2 MCL`, `SELL 1 CL`, `ALRT CL > 70`,
`ALRT NG SWING < -50`, `FLAT`.

**Universe:** CL NG RB HO · GC SI HG PL · ZC ZS ZW · KC SB CC CT · LE HE · MCL MGC (micros map to their parent's data).

## Signal methodology

Every factor is scored from −100 (bearish) to +100 (bullish).

* **Trend (TSMOM):** vol-normalised 20/60/120-day returns, blended through tanh.
* **Breakout:** position inside the 20-day Donchian channel.
* **Mean reversion:** 20-day z-score and RSI(14), faded.
* **Carry:** front vs second month, annualised. Backwardation is bullish. Needs a curve feed in live mode.
* **Seasonality:** average 20-day forward return for the same calendar window in prior years, weighted by hit rate.
* **COT:** managed-money 3-year index, read as contrarian at ≥90 / ≤10. The commercial index confirms.
  A weekly change ≥2σ is flagged.
* **Supply/demand:** stocks vs the 5-year same-week norm (z-score), plus the weekly surprise vs the normal change.
* **Swing composite:** trend 25%, COT 20%, S&D 20%, breakout 10%, carry 10%, seasonality 10%, mean reversion 5%.
  Weights are renormalised when a factor is unavailable.
* **Day composite:** VWAP deviation in ATRs 30%, breakout 25%, trend 25%, mean reversion 20%.
  Floor pivots, previous high/low and ATR in ticks and $/contract support execution.
* **Forecast:** shrunk momentum drift + AR(1) + seasonal drift, with an EWMA volatility cone.
  The walk-forward 5-day hit rate is shown next to it. Expect roughly 50%. The value is in the range, not the direction.

## Prop desk

`backend/app/prop/rules.py` models the rule families futures prop firms use:

* profit target, daily loss limit, minimum trading days
* max drawdown: **static**, **trailing intraday** or **trailing end-of-day**, with an optional lock at the starting balance
* contract caps (a micro counts as 1/10)
* consistency rule (best day ≤ X% of total profit)

Every order passes a **pre-trade check**: contract cap, plus whether the stop-out loss would breach the
drawdown buffer or the daily loss allowance. **Risk sizing** turns a stop in ticks and a "% of remaining
buffer" into contracts. Alerts fire at 75% of the daily loss used, within 25% of the drawdown, on a breach,
and when the target is hit but the consistency rule fails.

Brokers (`backend/app/prop/brokers.py`):

* `PaperBroker`: works out of the box, with OCO stop/target brackets.
* `TradovateAdapter` and `ProjectXAdapter`: REST skeletons written from the public API docs.
  **Not yet certified against live endpoints.** Test on demo credentials first.

Live routing stays disabled until `LIVE_TRADING_ENABLED=true`. Presets are generic templates. Users must
enter their own firm's current rules.

## API

REST: `/api/watchlist`, `/api/snapshot/{sym}`, `/api/history/{sym}`, `/api/intraday/{sym}`, `/api/cot[/{sym}]`,
`/api/supply-demand`, `/api/matrix`, `/api/alerts[/rules]`, `/api/prop[/presets|/rules|/reset|/size|/flatten]`,
`/api/orders`.

WebSocket: `/ws` streams `tick`, `signals`, `alert` and `fill` messages. Interactive docs are at `/docs`.

## Layout

```
backend/app/
  config.py, universe.py        settings, contract specs (tick size/value, CFTC codes, EIA series)
  data/sim.py, live.py, store.py simulator, CFTC/EIA/Yahoo adapters, in-memory market hub
  signals/quant.py, fundamental.py, forecast.py, engine.py
  alerts.py                     built-in + user rules, dedup/cooldown
  prop/rules.py, brokers.py     prop rule engine, paper/Tradovate/ProjectX adapters
  main.py                       FastAPI REST + WebSocket, background tick & signal loops
backend/tests/                  signals, prop rules, brackets, alerts, API end-to-end
frontend/                       vanilla JS terminal UI + vendored TradingView Lightweight Charts (Apache-2.0)
```

## Before you sell this: commercial checklist

1. **Licensed market data.** Real-time CME/ICE prices need exchange licences, and redistributing them to
   paying users needs a vendor agreement that covers display/redistribution (e.g. Databento, dxFeed,
   Barchart, or Rithmic/CQG via the user's own login). Yahoo data must not be resold. CFTC and EIA data are public.
2. **Regulation.** Selling trading signals or "alerts" to US customers can touch CTA registration with
   CFTC/NFA. The publisher exclusion depends on the advice being impersonal and not tailored. Get a
   securities/derivatives lawyer before launch. Keep the "not investment advice" disclaimers.
3. **Prop firm integration.** Most firms provision accounts on Tradovate, Rithmic, ProjectX-based
   platforms, NinjaTrader or CQG. Third-party apps often need vendor approval or conformance testing
   (Rithmic especially). Firm rules change often, so ship rule presets you can update without a release.
4. **Honest performance claims.** Show out-of-sample hit rates (as the forecast screen does), never
   cherry-picked backtests.

## Roadmap

* Licensed real-time futures feed with a full curve, so carry and calendar spreads work in live mode
* USDA WASDE / Crop Progress, LME/COMEX warehouse stocks: S&D for metals, grains and softs
* Economic and report calendar (EIA, WASDE, COT, FOMC) with news-blackout rules for prop accounts
* Users, auth, subscriptions (Stripe), per-user alert delivery (email, Telegram, Discord, push)
* Persistent storage (Postgres/Timescale) and a backtest screen for each signal
* Multi-account prop dashboard (copy-trading across evaluations), payout tracker
