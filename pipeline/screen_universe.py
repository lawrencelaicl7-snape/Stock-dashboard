"""Helper used to choose the stock universe (not run by the daily workflow).

Ranks a broad candidate pool per market by market cap, keeping only stocks whose
3-month average daily traded value (ADTV) clears a liquidity floor.

    py pipeline/screen_universe.py            # prints a table per market
    py pipeline/screen_universe.py --json     # also writes data/screen_results.json
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yfinance as yf

CANDIDATES = {
    "US": """AAPL MSFT NVDA GOOGL AMZN META AVGO TSLA BRK-B LLY JPM WMT V ORCL MA XOM NFLX COST
             JNJ HD PG ABBV BAC UNH PLTR AMD KO CVX GE CSCO IBM MU WFC PM TMUS CRM MS GS AXP
             LIN ABT MCD TSM INTC QCOM ADBE""".split(),
    "SG": """D05 O39 U11 Z74 C6L S68 Y92 BN4 S63 F34 J36 H78 C38U A17U 9CI G13 U96 C07 V03 BS6
             D01 N2IU M44U ME8U 5E2 S58 U14 C09 BUOU AJBU K71U T82U J69U E5H F99 CC3 YF8 AIY
             BSL 558""".split(),
    "MY": """1155 1295 1023 5347 5225 5819 1082 6947 4863 6012 5183 5681 6033 1961 5285 2445 4707
             3816 1066 1015 5398 4197 6742 4677 3182 4715 5296 7084 4065 7113 5211 5168 0166 3034
             5326 5246 1818 0128 0138 5139 2836 3689 7277 5263 8869 5099 1171 6888""".split(),
}
SUFFIX = {"US": "", "SG": ".SI", "MY": ".KL"}
# Liquidity floor in USD of average daily traded value (price x 3-month avg volume).
MIN_ADTV_USD = {"US": 200e6, "SG": 3e6, "MY": 2e6}


def fx_to_usd(ccy):
    if ccy in (None, "USD"):
        return 1.0
    try:
        return float(yf.Ticker(f"{ccy}USD=X").fast_info["last_price"])
    except Exception:
        return None


def fetch(symbol):
    for attempt in range(3):
        try:
            info = yf.Ticker(symbol).info
            return {
                "symbol": symbol,
                "name": info.get("longName") or info.get("shortName"),
                "sector": info.get("sector"),
                "currency": info.get("currency"),
                "market_cap": info.get("marketCap"),
                "avg_volume": info.get("averageVolume"),
                "price": info.get("regularMarketPrice") or info.get("previousClose"),
            }
        except Exception as exc:  # noqa: BLE001 - network flakiness
            err = exc
            time.sleep(2 * (attempt + 1))
    return {"symbol": symbol, "error": str(err)}


def screen(market):
    symbols = [c + SUFFIX[market] for c in CANDIDATES[market]]
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(fetch, symbols))
    fx = {}
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
    ok = sorted((r for r in rows if r.get("market_cap_usd")), key=lambda r: -r["market_cap_usd"])
    return ok, [r for r in rows if not r.get("market_cap_usd")]


def main():
    out = {}
    for market in CANDIDATES:
        ok, failed = screen(market)
        out[market] = ok
        print(f"\n=== {market}  (liquidity floor USD {MIN_ADTV_USD[market]/1e6:.0f}m/day) ===")
        picked = 0
        for r in ok:
            mark = ""
            if r["liquid"] and picked < 20:
                picked += 1
                mark = f"{picked:>2}"
            print(f"{mark:>2} {r['symbol']:<10} {(r['name'] or '')[:38]:<38} {(r['sector'] or '-')[:22]:<22} "
                  f"{r['currency']} mcap {r['market_cap']/1e9:9.1f}bn  (USD {r['market_cap_usd']/1e9:8.1f}bn)  "
                  f"ADTV USD {r['adtv_usd']/1e6:9.1f}m {'' if r['liquid'] else 'ILLIQUID'}")
        for r in failed:
            print("   FAILED", r["symbol"], r.get("error", "no market cap"))
    if "--json" in sys.argv:
        Path("data").mkdir(exist_ok=True)
        Path("data/screen_results.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
