# Data sources for natural gas (NG)

Status as of November 2026. "Connected" = already wired into the terminal (`DATA_MODE=live`).
Keys go in the `.env` file next to `README.md`. Never paste them into chats or commit them.

## Already connected, no key needed

| Data | Source | Freshness | Used for |
|---|---|---|---|
| Futures prices, 10 years daily | Yahoo `NG=F` (back-adjusted at rolls) | ~10 min delayed | Everything price-based |
| Intraday bars | Yahoo 1-min (30 days) + 5-min (60 days), stored and growing | ~10 min delayed | Insider, research, strategies, signals |
| Futures curve (18 months) | Yahoo per-contract (`NGF27.NYM`, …) | ~10 min delayed | Curve, spreads, H/J |
| Storage, weekly, by region | EIA `ir.eia.gov` | Thursdays 10:30 ET | Storage, S&D signal |
| Supply & demand balance, monthly + forecast | EIA Short-Term Energy Outlook (`STEO_m.xlsx`) | Monthly | Balance section |
| Weather: observed + 16-day forecast | Open-Meteo (12 gas-weighted cities) | Hourly | HDD/CDD, weather bias, weekly adjusted balance |
| Weather model runs: GFS vs ECMWF, change vs previous run | Open-Meteo previous-runs API | 4×/day | Model-run panel |
| COT positioning | CFTC disaggregated | Weekly (Fri) | COT screen and signals |
| News | Google News, Yahoo Finance, NGI, EIA Today in Energy, Rigzone, OilPrice RSS | Minutes | News feed, news signals |

Open-Meteo's free tier is 10,000 weighted calls/day and **non-commercial**. The terminal now downloads the
10-year weather history once and only tops it up, so normal use costs well under 1,000/day.

## Register for these (free), then add the key

| # | Service | Register | `.env` setting | What it unlocks |
|---|---|---|---|---|
| 1 | **EIA Open Data API** | https://www.eia.gov/opendata/register.php (instant, emailed) | `EIA_API_KEY=` | **Daily power burn** (EIA-930 gas-fired generation, ~1-day lag), **Henry Hub spot** and cash-futures basis, crude/gasoline/distillate stocks for CL/RB/HO |
| 2 | **Databento** | https://databento.com/signup (free credit on signup) | `DATABENTO_API_KEY=` (optional `DATABENTO_MAX_USD=5`, `DATABENTO_YEARS=2`) | **Years of real CME 1-minute NG bars** for every intraday backtest (Yahoo only gives 30 days). Costs cents to a few dollars per market; a spend cap is enforced |

After adding a key, restart. The **DATA SOURCES** panel on the Nat Gas Desk shows each feed as LIVE, KEY (missing) or ERROR.
Databento also runs from the command line: `cd backend` then `python -m app.tools.backfill NG --years 3 --dry-run` (shows the cost) and without `--dry-run` to download.

## Paid upgrades (when you're ready)

| Need | Options | Approx. cost |
|---|---|---|
| **Real-time** CME NG quotes and bars (no 10-min delay) | ProjectX / TopstepX API (via a Topstep account) · Tradovate API · Databento Live · Rithmic | $15–30/month (prop API) · Databento Live from ~$200/month |
| Daily pipeline flows: LNG feedgas by terminal, daily production, Mexico exports | Wood Mackenzie (Genscape), LSEG, Criterion, East Daley | $$$ (enterprise) |
| Option chains, open interest, implied vol for NG options | Databento (GLBX.MDP3 options, same key) · CME DataMine · CME QuikStrike exports | Databento pay-as-you-go |
| Licensed price data to **resell** to customers | CME market-data licence via a distributor (Databento, dxFeed, Barchart) | Depends on licence |
| Commercial weather API (required when you sell) | Open-Meteo commercial · DTN · Atmospheric G2 | from ~€29/month |
| Premium energy news with sentiment | Benzinga, Reuters/LSEG, Bloomberg | $$ |

## Checked and not usable

* **Baker Hughes rig count**: the site blocks automated downloads (403). Download the weekly file by hand if needed.
* **GDELT news API**: unreliable connections; the RSS set above covers NG news better.
* **EIA Natural Gas Weekly Update page**: the supply/demand table there was stale (January placeholder values), so the
  monthly STEO plus the weekly weather-adjusted storage balance are used instead.
* **Barchart / CME websites**: scraping breaks their terms of use.
