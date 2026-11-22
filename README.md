# CMDTY//DESK: Commodities Terminal for Prop Traders

A live, terminal-style dashboard for commodity and equity-index futures **day trading and swing trading**. It combines
quantitative signals (trend, breakout, mean reversion, carry, seasonality, volatility regime) with
quantamental ones (CFTC COT positioning, inventory supply/demand vs 5-year norms). It adds forecasts,
real-time alerts, and a **prop desk** that tracks evaluation/funded-account rules and blocks trades
that would breach them.

> Working name. Do a trademark search before you pick the commercial brand.

![Day-Trade Insider](docs/screenshots/00-insider-es-top.png)

More screenshots in [`docs/screenshots/`](docs/screenshots): news, COT, signal matrix, prop desk, backtest lab,
optimiser, the classic theme and mobile.

**Themes:** *Nightfall* (default) is a graphite-navy design with a teal accent, sans-serif labels and monospaced
numbers. *Classic* is an amber-on-black terminal look. Switch between them with the ◐ THEME button.

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
| COT positioning | CFTC Public Reporting API: Disaggregated (commodities), Traders in Financial Futures (indices) | none | weekly (Tue data, Fri release) |
| Inventories | EIA Open Data v2 (crude, gasoline, distillate, nat gas storage) | free | weekly |
| Prices | Yahoo Finance chart API | none | **prototype only**: delayed, not licensed for redistribution |

## Screens (F-keys or the command line)

| Key | Screen | What it shows |
|---|---|---|
| ◆ (`DT`) | DAY-TRADE INSIDER | Landing page. Answers "what do I need to know today to day trade this market?": game plan, key levels, volume profile, regime, expected move, catalysts, ranked playbook, risk plan |
| F1 | MONITOR | Watchlist with swing/day composites, COT index and 5-day up-probability. Daily or 1-min candles with pivots, VWAP and a 20-day forecast cone |
| F2 | COT | Managed-money vs commercial net positions, COT index with crowded-long/short bands, cross-market table |
| F3 | SUPPLY/DEMAND | Stocks vs 5-yr same-week average, deviation σ, weekly surprise vs the normal change |
| F4 | FORECAST | 5d/20d targets, 1σ/2σ ranges, P(up), model components and the **walk-forward hit rate** |
| F5 | SIGNAL MATRIX | Every factor × every market as a heat map |
| F6 | PROP DESK | Rule editor and presets, drawdown/daily-loss/target meters, risk sizer, bracket order ticket, positions, trade log |
| F7 | ALERTS | Built-in triggers plus custom rules |
| F8 | HELP | Commands and methodology |
| F9 | BACKTEST LAB | Run, compare, optimise, walk-forward and Monte Carlo strategies, with a prop-evaluation simulator |
| F10 | NEWS | Market-linked headlines, each tagged LONG/SHORT per market with ★–★★★ importance; per-market news sentiment |

Command line examples: `CL`, `NG COT`, `GC FCST`, `BUY 2 MCL`, `SELL 1 CL`, `ALRT CL > 70`,
`ALRT NG SWING < -50`, `FLAT`, `BT`, `NQ NEWS`.

**Universe:** ES NQ YM (+ micros MES MNQ MYM) · CL NG RB HO · GC SI HG PL · ZC ZS ZW · KC SB CC CT · LE HE · MCL MGC (micros map to their parent's data).

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
* **Day composite:** VWAP deviation in ATRs 25%, trend 25%, breakout 20%, mean reversion 15%, news flow 15%.
  Floor pivots, previous high/low and ATR in ticks and $/contract support execution.
* **Forecast:** shrunk momentum drift + AR(1) + seasonal drift, with an EWMA volatility cone.
  The walk-forward 5-day hit rate is shown next to it. Expect roughly 50%. The value is in the range, not the direction.

## News feed (F10)

Every headline is mapped to the futures markets it moves. For each market it gets:
* **Signal:** LONG (bullish), SHORT (bearish) or NEUTRAL.
* **Importance:**
  * ★★★: tier-1 scheduled releases or shocks (Fed decisions, CPI, payrolls, OPEC+, EIA/USDA/WASDE reports,
    wars, sanctions, outages, export or import bans, hurricanes).
  * ★★: real fundamental news (inventories, export sales, crop conditions, Fed guidance, earnings of index heavyweights).
  * ★: commentary, previews, analyst opinion, or price-move stories.
* **Reasons:** hover a market tag to see the exact phrases that drove the call.

3★ headlines raise a CRITICAL alert and a toast, and 2★ headlines an INFO alert.

Each market also gets a **news sentiment** score: Σ direction × importance, halving every 12 h, scaled to −100…+100.
It appears in the monitor's NEWS column and the signal stack, and feeds the intraday composite at 15% weight.

| Setting | Options |
|---|---|
| Source | `DATA_MODE=sim`: simulated newswire (labelled SIM). `live`: Google News and Yahoo Finance RSS per market, plus any `NEWS_FEEDS`. |
| Classifier | `NEWS_CLASSIFIER=rules` (default): free, instant, explainable keyword model. `claude`: Claude reads batches of 20 headlines and returns schema-validated JSON, including second-order effects. If the call fails, it falls back to rules. Roughly a few cents per batch on the default model; `NEWS_LLM_MODEL` overrides it. |

RSS gives headlines and links only, with minutes of latency, and its terms usually forbid redistribution.
For a paid product, license a real-time newswire (e.g. Benzinga, Dow Jones, MT Newswires) and plug it in
next to `feeds.fetch_live`.

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

## Day-Trade Insider (default screen)

A pre-session briefing for each market, written the way a day trader prepares:

* **Game plan:** 6-7 plain-language bullets covering:
  * the regime and how to trade it;
  * the bias versus VWAP and yesterday's range;
  * the nearest resistance and support (in ticks);
  * the size of a normal day and how much is already used;
  * today's catalysts, the best-fit setup, and position size.
* **Pre-session checklist:** saved per day and market.
* **Key-level ladder:**
  * prior-day high/low/settle, today's open/high/low, VWAP and floor pivots;
  * prior 5-day and 20-day extremes, round numbers;
  * volume POC and value area.

  Each level shows its distance in ticks and ATR, with the live price marked.
* **Volume profile:** a composite of 20 sessions with POC, value area (70%), high-volume nodes ("magnets")
  and low-volume nodes ("fast moves"), plus where price sits relative to value. It is estimated from daily
  bars; a tick feed gives the true profile.
* **Regime:** ADX, efficiency ratio and Choppiness Index classify the market as TRENDING, RANGE-BOUND or
  TRANSITION, with what tends to work in each.
* **Expected move:**
  * 20-day average daily range, typical (p50) and big (p80) days;
  * the share of a normal range already used, and the projected high and low if today is an average day.
* **Seasonality:** weekday and month behaviour, the next 20 days in prior years, model P(up), COT state.
* **Catalysts:** the scheduled reports for this market and day (times ET; estimated dates are flagged),
  U.S. exchange holidays and early closes, and ★★+ news from the last 36 h.
* **Playbook, ranked for today's regime:** six single-session setups, each with today's exact trigger, stop
  and target prices, and an ACTIVE / NOT TODAY flag:
  * prior-day high/low breakout, gap-and-go, trend open-drive;
  * gap fade, pivot S1/R1 fade, inside-day/NR7 breakout.

  Each is backtested on ~3 years for this market, after costs, split by the regime known before each
  session. The verdict (PREFERRED / WORKS / MARGINAL / AVOID) requires both profit factor and t-stat.
  When nothing has an edge, the plan says so: sitting out is a valid call.
* **Risk plan:**
  * a size multiplier (cut for high volatility, ★★★ event days and unclear regimes);
  * a stop of about 25% of the average daily range with a 2R target;
  * risk per trade of 25% of the remaining daily-loss allowance, in full-size and micro contracts;
  * a daily stop, max trades, the consistency-rule profit cap, event and time-of-day rules.

  "Load plan into order ticket" pre-fills the prop desk.

The day-trade setups are tested on daily bars with the standard OHLC path model (up days trade
open→low→high→close, down days open→high→low→close). This is an approximation, so verify a setup on intraday
data before risking money on it.

## Backtest lab (F9)

**Strategies.** Each strategy declares its parameters, so the UI builds the input form and the
optimisation grid automatically. Every strategy can be set to long/short, long-only or short-only.

| Family | Strategies |
|---|---|
| Trend | MA crossover · Donchian breakout (turtle) · Time-series momentum |
| Mean reversion | RSI reversion · Bollinger band reversion |
| Quantamental | COT contrarian · Trend + COT crowding filter · Inventory supply/demand (energy) |

**Execution model.**
* A signal at a bar's close is filled at the **next bar's open**, so there is no look-ahead.
* Commission and slippage (in ticks) are charged per contract per side.
* Optional ATR stop-loss and take-profit. A gap through a level fills at the open, and there is no
  instant re-entry after a stop.
* Sizing is fixed contracts, or risk-based (account × risk% ÷ (ATR × $/pt)) with a cap.
* COT data is usable from its Friday release. EIA data is usable from the Wednesday after the week it covers.

**Tools.**
* **Run:** equity vs buy & hold, drawdown, full metrics (Sharpe, Sortino, Calmar, profit factor, win
  rate, exposure…), trade list, monthly P&L heatmap.
* **Prop evaluation simulator:** starts a new evaluation every 5 days and replays the strategy through
  the chosen firm rules. Reports pass %, the fail split (drawdown vs daily loss), timeouts and median
  days to pass.
* **Compare:** overlays up to 3 earlier runs.
* **Optimise:** grid search of up to 600 combinations over any parameters, with an out-of-sample holdout.
  It shows the top results, a 2-D heatmap, and overfitting diagnostics: in-sample/out-of-sample rank
  correlation, share of profitable combinations, and the best combination's out-of-sample/in-sample ratio.
  Click a row to load and run it.
* **Walk-forward:** rolling or anchored. Re-optimises on each training window and trades the winner on
  the next unseen window. Reports the stitched out-of-sample equity, WF efficiency and parameter stability.
* **Monte Carlo:** resamples the trade sequence to give final P&L and max-drawdown percentiles, plus
  the probability of breaching the prop account's max drawdown.

To add a strategy, write a single function plus one `Strategy(...)` entry in `backend/app/backtest/strategies.py`.
The UI picks it up automatically.

## API

REST: `/api/watchlist`, `/api/snapshot/{sym}`, `/api/history/{sym}`, `/api/intraday/{sym}`, `/api/cot[/{sym}]`,
`/api/supply-demand`, `/api/matrix`, `/api/alerts[/rules]`, `/api/prop[/presets|/rules|/reset|/size|/flatten]`,
`/api/orders`, `/api/backtest/{strategies|run|optimize|walkforward|montecarlo}`, `/api/news[/scores|/status]`, `/api/insider/{sym}`.

WebSocket: `/ws` streams `tick`, `signals`, `alert`, `fill` and `news` messages. Interactive docs are at `/docs`.

## Layout

```
backend/app/
  config.py, universe.py        settings, contract specs (tick size/value, CFTC codes, EIA series)
  data/sim.py, live.py, store.py simulator, CFTC/EIA/Yahoo adapters, in-memory market hub
  signals/quant.py, fundamental.py, forecast.py, engine.py
  news/feeds.py, classifier.py, llm.py, service.py  RSS + sim feeds, rule & Claude classifiers, scoring
  daytrade/calendar.py, analytics.py, playbook.py, insider.py  report calendar, regime/profile/ranges, setups, briefing
  alerts.py                     built-in + user rules, dedup/cooldown
  backtest/strategies.py, engine.py  strategy library; simulator, metrics, optimiser, walk-forward, Monte Carlo
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
* Persistent storage (Postgres/Timescale), saved backtests and shareable strategy reports
* Portfolio backtests (one strategy across many markets), intraday-bar backtests, custom strategy builder
* Multi-account prop dashboard (copy-trading across evaluations), payout tracker
