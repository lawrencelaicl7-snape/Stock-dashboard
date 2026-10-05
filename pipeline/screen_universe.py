"""Helper used to choose the stock universe (not run by the daily workflow).

For each market, a broad candidate pool is screened in two steps:

1. Liquidity hurdle (pass/fail only): 3-month average daily traded value (ADTV)
   must clear MIN_ADTV_USD. Liquidity plays no further part in the ranking.
2. Ranking: SIZE_WEIGHT x market-cap percentile + QUALITY_WEIGHT x Quality score,
   both computed within the market's candidate pool. Quality uses the same
   profitability / cash-flow / growth definition as the dashboard
   (analytics.quality_components).

The top 20 per market are proposed. Nothing is changed automatically: copy the
picks you approve into config/universe.json.

    python pipeline/screen_universe.py            # prints a table per market
    python pipeline/screen_universe.py --json     # also writes data/screen_results.json

On GitHub: Actions -> "Screen stock universe" -> Run workflow (results on the run's summary page).
"""
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
import analytics  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = {
    "US": """AAPL MSFT NVDA GOOGL AMZN META AVGO TSLA BRK-B LLY JPM WMT V ORCL MA XOM NFLX COST
             JNJ HD PG ABBV BAC UNH PLTR AMD KO CVX GE CSCO IBM MU WFC PM TMUS CRM MS GS AXP
             LIN ABT MCD TSM INTC QCOM ADBE NOW ISRG UBER TXN AMAT LRCX KLAC ANET BKNG PEP
             TMO ACN SPGI CAT""".split(),
    "SG": """D05 O39 U11 Z74 C6L S68 Y92 BN4 S63 F34 J36 H78 C38U A17U 9CI G13 U96 C07 V03 BS6
             D01 N2IU M44U ME8U 5E2 S58 U14 C09 BUOU AJBU K71U T82U J69U E5H F99 CC3 YF8 AIY
             BSL 558""".split(),
    "MY": """1155 1295 1023 5347 5225 5819 1082 6947 4863 6012 5183 5681 6033 1961 5285 2445 4707
             3816 1066 1015 5398 4197 6742 4677 3182 4715 5296 7084 4065 7113 5211 5168 0166 3034
             5326 5246 1818 0128 0138 5139 2836 3689 7277 5263 8869 5099 1171 6888""".split(),
}
SUFFIX = {"US": "", "SG": ".SI", "MY": ".KL"}
# Liquidity hurdle in USD of average daily traded value (price x 3-month avg volume).
MIN_ADTV_USD = {"US": 200e6, "SG": 3e6, "MY": 2e6}
# Ranking weights among stocks that clear the hurdle (must sum to 1).
SIZE_WEIGHT = 0.5
QUALITY_WEIGHT = 0.5
PICKS = 20


def _pct(x):
    return None if x is None else float(x) * 100


def fx_to_usd(ccy):
    if ccy in (None, "USD"):
        return 1.0
    try:
        return float(yf.Ticker(f"{ccy}USD=X").fast_info["last_price"])
    except Exception:  # noqa: BLE001
        return None


def fetch(symbol):
    err = None
    for attempt in range(3):
        try:
            info = yf.Ticker(symbol).info
            row = {
                "symbol": symbol,
                "name": info.get("longName") or info.get("shortName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "currency": info.get("currency"),
                "market_cap": info.get("marketCap"),
                "avg_volume": info.get("averageVolume"),
                "price": info.get("regularMarketPrice") or info.get("previousClose"),
            }
            row.update(analytics.quality_inputs(
                market_cap=info.get("marketCap"), fcf=info.get("freeCashflow"), ocf=info.get("operatingCashflow"),
                net_income=info.get("netIncomeToCommon"), roe=_pct(info.get("returnOnEquity")),
                roa=_pct(info.get("returnOnAssets")), op_margin=_pct(info.get("operatingMargins")),
                revenue_growth=_pct(info.get("revenueGrowth")), earnings_growth=_pct(info.get("earningsGrowth"))))
            return row
        except Exception as exc:  # noqa: BLE001 - network flakiness
            err = exc
            time.sleep(2 * (attempt + 1))
    return {"symbol": symbol, "error": str(err)}


def screen(market):
    symbols = [c + SUFFIX[market] for c in CANDIDATES[market]]
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(fetch, symbols))
    fx = {}
    ok = []
    for r in rows:
        if r.get("error") or not r.get("market_cap"):
            continue
        ccy = r["currency"]
        if ccy not in fx:
            fx[ccy] = fx_to_usd(ccy)
        rate = fx[ccy] or 0
        r["market_cap_usd"] = r["market_cap"] * rate
        r["adtv_usd"] = (r["avg_volume"] or 0) * (r["price"] or 0) * rate
        r["liquid"] = r["adtv_usd"] >= MIN_ADTV_USD[market]
        ok.append(r)
    liquid = [r for r in ok if r["liquid"]]
    # Quality and size percentiles are computed among stocks that clear the hurdle.
    quality = analytics.quality_scores(analytics.quality_components(liquid))
    size = analytics.percentile([math.log(r["market_cap_usd"]) for r in liquid])
    for r, q, sz in zip(liquid, quality, size):
        r["quality"] = q
        r["size_pct"] = round(sz, 1)
        r["score"] = round(SIZE_WEIGHT * sz + QUALITY_WEIGHT * (q if q is not None else 50), 1)
    liquid.sort(key=lambda r: -r["score"])
    for i, r in enumerate(liquid):
        r["rank"] = i + 1
        r["picked"] = i < PICKS
    illiquid = sorted((r for r in ok if not r["liquid"]), key=lambda r: -r["market_cap_usd"])
    failed = [r for r in rows if r.get("error") or not r.get("market_cap")]
    return liquid, illiquid, failed


def _f(v, fmt="{:.1f}", suffix=""):
    return "—" if v is None else fmt.format(v) + suffix


def report(market, liquid, illiquid, failed, current):
    lines = [f"## {market}", "",
             f"Liquidity hurdle: ADTV ≥ USD {MIN_ADTV_USD[market] / 1e6:g}m/day · "
             f"ranking = {SIZE_WEIGHT:.0%} size + {QUALITY_WEIGHT:.0%} quality", "",
             "| # | Ticker | Name | Sector | Mkt cap (USD bn) | Size pct | ROE | FCF yld | Rev g | EPS g | Quality | Score | ADTV (USD m) | Now in list? |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in liquid:
        exempt = analytics.cash_flow_exempt(r)
        lines.append(
            f"| {r['rank'] if r['picked'] else '(' + str(r['rank']) + ')'} | {r['symbol']} | {(r['name'] or '')[:36]} | "
            f"{r['sector'] or '—'} | {r['market_cap_usd'] / 1e9:,.1f} | {r['size_pct']:.0f} | {_f(r['roe'], suffix='%')} | "
            f"{'n.m.' if exempt else _f(r['fcf_yield'], suffix='%')} | {_f(r['revenue_growth'], '{:+.1f}', '%')} | "
            f"{_f(r['earnings_growth'], '{:+.0f}', '%')} | {_f(r['quality'], '{:.0f}')} | {r['score']:.0f} | "
            f"{r['adtv_usd'] / 1e6:,.1f} | {'yes' if r['symbol'] in current else '**new**' if r['picked'] else ''} |")
    picked = {r["symbol"] for r in liquid if r["picked"]}
    dropped = sorted(current - picked)
    lines += ["", f"**Would join:** {', '.join(sorted(picked - current)) or 'none'}  ",
              f"**Would leave:** {', '.join(dropped) or 'none'}", ""]
    if illiquid:
        lines += ["Below liquidity hurdle: " + ", ".join(
            f"{r['symbol']} (USD {r['adtv_usd'] / 1e6:,.1f}m/day)" for r in illiquid), ""]
    if failed:
        lines += ["No data: " + ", ".join(r["symbol"] for r in failed), ""]
    return "\n".join(lines)


def main():
    cfg = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    out, md = {}, ["# Stock universe screen", "",
                   "Rows in brackets are ranked but outside the top 20. Quality = profitability (ROE, ROA, operating "
                   "margin), cash flow (FCF yield, cash conversion; n.m. for banks/insurers) and growth (revenue, "
                   "earnings), equal-weighted, as percentiles within the market's candidates.", ""]
    for market in CANDIDATES:
        liquid, illiquid, failed = screen(market)
        current = {s["symbol"] for s in cfg["markets"][market]["stocks"]}
        out[market] = liquid
        section = report(market, liquid, illiquid, failed, current)
        md.append(section)
        print(section, "\n")
    text = "\n".join(md) + "\n"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    if "--json" in sys.argv:
        (ROOT / "data").mkdir(exist_ok=True)
        (ROOT / "data" / "screen_results.json").write_text(json.dumps(out, indent=1, default=str))
        (ROOT / "data" / "screen_results.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
