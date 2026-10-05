# Stock Focus Dashboard

A free, static dashboard that tracks the **20 largest liquid stocks** in three markets — **US**, **Singapore (SGX)** and **Malaysia (Bursa)** — and highlights what deserves attention today using transparent factor scores and rule-based signals.

**Live site:** `https://<your-username>.github.io/<repo-name>/` (see *Settings → Pages*)

> Rule-based signals for education — not investment advice. Data comes from free, unofficial sources and may be delayed, incomplete or wrong.

---

## What's on the page

| Section | What it does |
|---|---|
| **Tabs** | All / US / Singapore / Malaysia |
| **Focus score weights** | Six sliders (one per factor), presets *Balanced*, *Momentum*, *Value*, *Quality*, *Event-driven*, and *Reset*. Your last weights are remembered in this browser. |
| **Filters** | Search, sector, minimum focus score, earnings within N days. Filters apply to every panel. |
| **What to focus on today** | Top 5 per market by focus score, each with plain-English reasons and their trigger values. |
| **Heat map** | Tile size = market cap; colour = 1-day %, 1-week %, 1-month %, relative volume, focus score or quality score. Blue = higher, red = lower, grey = neutral. In the *All* view each market gets equal area so SG and MY stay readable. |
| **All metrics table** | Click a column header to sort, click a row for details. |
| **Detail panel** | Price chart (1M/3M/1Y) with 50- and 200-day moving averages and volume, factor-score bars, key ratios (incl. ROE, ROA, margin, free cash flow, growth), earnings, social attention, latest headlines and research links. |

Prices are always in the stock's **trading currency** (USD, SGD or MYR). Hongkong Land (H78) and Jardine Matheson (J36) trade in **USD** on SGX and are labelled USD. A **"Data as of"** timestamp sits at the top right; it turns into a ⚠ *Stale data* badge if the data is more than 2 days old.

---

## How to edit the stock lists

Everything lives in **[`config/universe.json`](config/universe.json)**. Each market has a `stocks` list:

```json
{"symbol": "D05.SI", "code": "D05", "name": "DBS Group", "news_query": "\"DBS\" bank"}
```

| Field | Required | Meaning |
|---|---|---|
| `symbol` | yes | Yahoo Finance ticker. US: `AAPL`; Singapore: add `.SI` (e.g. `D05.SI`); Malaysia: add `.KL` (e.g. `1155.KL`). Check it opens on finance.yahoo.com first. |
| `name` | yes | Short display name. |
| `code` | SG/MY | Exchange stock code (SGX `D05`, Bursa `1155`) used for links. |
| `bursa_name` | MY | Bursa short name (e.g. `MAYBANK`) — used for the StockAnalysis link and heat-map labels. Find it on `theedgemalaysia.com/askedge/klse/<code>`. |
| `sgx_name` | optional | Exact SGX-registered company name for the SGX announcements link. Defaults to Yahoo's full company name in capitals, which matched SGX in our tests. |
| `news_query` | optional | Custom Google News search when the name is ambiguous (e.g. `"Visa Inc" OR "Visa stock"`). |

**To edit on GitHub (no software needed):** open `config/universe.json` in the repo → click the ✏️ pencil → edit → **Commit changes**. The new list is picked up on the next scheduled run, or trigger one manually (below).

### How the lists are chosen

The screener [`pipeline/screen_universe.py`](pipeline/screen_universe.py) proposes the 20 stocks per market in two steps:

1. **Liquidity hurdle (pass/fail only).** 3-month average daily traded value must be at least USD 200m (US), USD 3m (SG) or USD 2m (MY). Liquidity plays no further part — it only makes sure the stock can be traded easily.
2. **Ranking.** Among stocks that pass: **50% size** (market-cap percentile) + **50% Quality score** (profitability, cash flow and growth — same definition as the dashboard factor below). The top 20 are proposed.

Run it on GitHub: **Actions → Screen stock universe → Run workflow**. The proposed lists, with what would join and leave, appear on the run's summary page. It never edits `config/universe.json` — copy the picks you agree with yourself. The weights, liquidity hurdles and candidate pools are at the top of the script.

---

## How the scores are calculated

Each factor is a **percentile from 0 to 100 within the stock's own market** (a US stock is only compared with the other 19 US stocks). A factor built from several inputs is the average of the input percentiles; missing inputs are skipped rather than guessed.

| Factor | Inputs (higher percentile = …) |
|---|---|
| **Momentum** | 1M, 3M, 6M total return; RSI(14); price vs 50-day MA; price vs 200-day MA (stronger trend) |
| **Volume** | Relative volume = latest session volume ÷ average of the previous 20 sessions (more unusual activity) |
| **Value** | Earnings yield (1/PE), book yield (1/PB), dividend yield (cheaper / higher income). Loss-making companies rank lowest on earnings yield. |
| **Attention** | News headlines in the last 7 days; for US also Reddit mentions (24h) and StockTwits messages/day (log-scaled) |
| **Quality** | Three equally weighted groups: **profitability** (ROE, ROA, operating margin), **cash flow** (free-cash-flow yield = FCF ÷ market cap; cash conversion = operating cash flow ÷ net income, only when profitable) and **growth** (revenue and earnings growth, year on year). For **banks and insurers** the cash-flow group is skipped, because their cash flows mostly reflect deposits, loans and insurance float; the table shows "n.m." (not meaningful). |
| **Reporting** | Days to next earnings (sooner = higher; unknown = lowest) and last EPS surprise % (bigger beat = higher) |

**Focus score** = Σ (weight × factor score) ÷ Σ weights, computed live in your browser so the sliders apply instantly. If a factor is missing for a stock, its weight is redistributed over the remaining factors.

| Preset | Momentum | Volume | Value | Quality | Attention | Reporting |
|---|---|---|---|---|---|---|
| Balanced (default) | 20 | 20 | 20 | 20 | 20 | 20 |
| Momentum | 40 | 15 | 5 | 15 | 15 | 10 |
| Value | 10 | 5 | 45 | 30 | 5 | 5 |
| Quality | 15 | 5 | 20 | 50 | 5 | 5 |
| Event-driven | 10 | 15 | 5 | 10 | 25 | 35 |

Volume (trading activity) is never the largest weight in any preset. Weights are relative: six sliders at 20 each means each factor counts 1/6.

### Rule-based reasons ("What to focus on today")

Generated in `pipeline/analytics.py` → `build_signals()`. Current rules:

- Relative volume ≥ 1.5× → "Volume 2.8x its 20-day average"
- Earnings within 14 days → "Earnings in 3 days (2026-10-07)"
- Price crossed the 50- or 200-day MA within the last 5 sessions; golden/death cross within 10 sessions
- New 52-week high, within 3% of the 52-week high, or within 5% of the 52-week low
- RSI ≥ 70 (overbought) or ≤ 30 (oversold)
- 1-day move ≥ 3%; 1-month move ≥ 10%
- Reddit mentions at least doubled vs the previous 24h (min. 10 mentions), or ≥ 50 mentions
- News count in the top 20% of the market (min. 10)
- ROE ≥ 20% ("High profitability"); free-cash-flow yield negative or ≥ 6% (not for banks/insurers); earnings growth ≥ ±25% year on year
- Last EPS surprise ≥ ±5%; analyst mean target ≥ ±15% from price (≥ 3 analysts); dividend yield ≥ 6%

---

## Data sources and their limits

| Data | Source | Limits |
|---|---|---|
| Prices, volume (2y daily), market cap, P/E, P/B, dividend yield, 52-week range, sector, analyst target & rating | Yahoo Finance via the [`yfinance`](https://github.com/ranaroussi/yfinance) library | Unofficial and free; Yahoo can change or throttle it without notice. Prices are end-of-day and may be delayed. Analyst coverage for SG/MY is thinner. Sector labels are Yahoo's (e.g. KLK shows as Industrials). |
| ROE, ROA, operating margin, free & operating cash flow, net income, revenue & earnings growth | yfinance `.info` | Trailing-12-month figures (growth = latest quarter vs a year earlier), as reported by Yahoo; some SG/MY fields are missing and are skipped rather than guessed. ROE and ROA differ structurally between sectors (banks have low ROA by nature), and the factor compares all sectors within a market together. Free cash flow can be lumpy for companies with large one-off capital spending. |
| Next earnings date, EPS surprise | yfinance (`calendar`, `get_earnings_dates`) | Some SG/MY companies have no published date or surprise history → shown as "—". Semi-annual reporters naturally score lower on "days to earnings". |
| News headlines | yfinance first; **Google News RSS** fallback (last 7 days) | Yahoo's news feed returned nothing during testing, so headlines currently come from Google News. Google returns at most **100** items, so busy US names show "100+" and tie on news count. Headlines are matched by company-name search and can occasionally include unrelated stories. |
| Reddit mentions (US only) | [ApeWisdom](https://apewisdom.io/) public API | Counts posts/comments on stock subreddits over 24h; noisy and meme-driven. |
| StockTwits activity (US only) | StockTwits public symbol stream | Estimates messages/day from the latest 30 messages; unofficial endpoint that may stop working. |
| Social data for SG/MY | — | Not available from free sources → shown as **N/A**, never estimated. |
| FX (heat-map sizing only) | Yahoo Finance FX | Used to convert market caps to USD so tile sizes are comparable. |

Every run writes `data/stocks.json` with a `generated_at` timestamp, a `sources` block describing where each field came from, and an `errors` list.

**Robustness:** every network call is retried with back-off. If a stock fails completely, its previous values are kept and it is flagged **STALE** (with the time of its last good update). Partial failures (e.g. fundamentals only) keep the previous fundamentals. The run only fails if *no* stock could be fetched. A log of each run is attached to the workflow run as `pipeline-log`.

**Research links** in the detail panel use URL patterns verified in October 2026: Yahoo Finance, StockAnalysis, Finviz (US), Morningstar, Bloomberg, SGX announcements and The Business Times (SG), Bursa announcements and The Edge Malaysia (MY). Websites change their URLs over time; if one breaks, edit `links()` in `assets/app.js`.

To re-check every link, run **Actions → Check research links → Run workflow**. It fetches each URL from GitHub's servers and lists the HTTP status and page title on the run's summary page, so you can see whether each link lands on the right company. Some sites (notably Bloomberg) refuse automated requests and show as *blocked*; check those by clicking them in your browser. The checker's URL rules live in `pipeline/check_links.py` and must be kept in step with `links()` in `assets/app.js`.

---

## How the automatic refresh works

The workflow [`.github/workflows/update.yml`](.github/workflows/update.yml) runs on GitHub's servers:

| When (UTC) | Local time | Why |
|---|---|---|
| 10:00 Mon–Fri | 18:00 SGT/MYT | After SGX and Bursa close |
| 22:00 Mon–Fri | 18:00 New York (EDT) | After the US close |

Each run installs Python, runs `pipeline/fetch_data.py`, commits the updated `data/stocks.json`, and redeploys the site to GitHub Pages. Scheduled runs can start a few minutes late when GitHub is busy. GitHub pauses schedules in repositories with no activity for 60 days; the data commits normally count as activity, but if the schedule stops, re-enable it on the Actions tab.

### Trigger a manual refresh

1. Open the repository on GitHub → **Actions** tab.
2. Click **Update data and deploy** in the left sidebar.
3. Click **Run workflow** → **Run workflow**.
4. Wait about 3–5 minutes for both jobs to show a green tick, then reload the site.

---

## Run it on your own computer (optional)

```bash
pip install -r requirements.txt
python pipeline/fetch_data.py      # writes data/stocks.json (~2 minutes)
python -m http.server 8765         # then open http://localhost:8765
```

## Project layout

```
config/universe.json       stock lists (edit this)
pipeline/fetch_data.py     downloads data, handles retries and stale fallback
pipeline/analytics.py      indicators, factor percentiles, rule-based signals
pipeline/screen_universe.py proposes the lists: liquidity hurdle, then size + quality ranking
pipeline/check_links.py    checks the research-link URL patterns (run via Actions)
data/stocks.json           output consumed by the website
index.html, assets/        the static website (ECharts from cdnjs)
.github/workflows/         scheduled refresh + GitHub Pages deploy
```
