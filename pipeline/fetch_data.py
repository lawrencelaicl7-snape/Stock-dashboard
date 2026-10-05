"""Daily data pipeline: config/universe.json  ->  data/stocks.json

    py pipeline/fetch_data.py

Free sources only: Yahoo Finance (via yfinance), Google News RSS (fallback for
headlines), ApeWisdom (Reddit mentions, US only) and StockTwits public stream
(US only). A stock that fails keeps its previous values and is flagged stale.
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
import analytics  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "universe.json"
OUT = ROOT / "data" / "stocks.json"
LOG = ROOT / "data" / "pipeline.log"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      "Accept": "application/json, text/xml, */*"}

SOURCES = {
    "prices": "Yahoo Finance via yfinance (daily OHLCV, 2y; returns use dividend-adjusted closes)",
    "fundamentals": "Yahoo Finance via yfinance .info (market cap, P/E, P/B, dividend yield, 52w range, sector, analyst target & rating)",
    "quality": "Yahoo Finance via yfinance .info (ROE, ROA, operating margin, free & operating cash flow, net income, revenue & earnings growth; trailing 12 months / latest quarter year-on-year)",
    "earnings": "Yahoo Finance via yfinance (.calendar for next date, .get_earnings_dates for EPS surprise)",
    "news": "Yahoo Finance via yfinance; falls back to Google News RSS search (last 7 days) when Yahoo returns nothing",
    "reddit": "ApeWisdom public API (Reddit mentions, last 24h vs previous 24h) — US only",
    "stocktwits": "StockTwits public symbol stream (latest 30 messages) — US only",
    "fx": "Yahoo Finance FX rates (used only to size the 'All markets' heat map in USD)",
}

log = logging.getLogger("pipeline")


def setup_logging():
    LOG.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG, mode="w", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def retry(fn, *args, tries=3, wait=2.0, what="call", **kwargs):
    """Run fn with exponential backoff; returns None (and logs) if every attempt fails."""
    for attempt in range(1, tries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if attempt == tries:
                log.warning("%s failed after %d tries: %s", what, tries, exc)
                return None
            time.sleep(wait * 2 ** (attempt - 1))


def clean(x):
    """Make a value JSON-safe (NaN/inf -> None, numpy -> python)."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        x = x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


# ---------------------------------------------------------------- prices
def download_prices(symbols: list[str]) -> dict[str, pd.DataFrame]:
    def _dl(batch):
        df = yf.download(batch, period="2y", interval="1d", auto_adjust=False, group_by="ticker",
                         progress=False, threads=True)
        if df is None or df.empty:
            raise RuntimeError("empty download")
        return df

    out = {}
    for i in range(0, len(symbols), 20):
        batch = symbols[i:i + 20]
        df = retry(_dl, batch, what=f"price download {batch[0]}..")
        for sym in batch:
            hist = None
            if df is not None and sym in df.columns.get_level_values(0):
                hist = _tidy(df[sym])
            if hist is None or len(hist) < 30:  # retry individually
                one = retry(lambda s: yf.Ticker(s).history(period="2y", auto_adjust=False), sym,
                            what=f"history {sym}")
                hist = _tidy(one) if one is not None else None
            if hist is not None and len(hist) >= 30:
                out[sym] = hist
            else:
                log.error("No price history for %s", sym)
    return out


def _tidy(df):
    if df is None or df.empty:
        return None
    df = df.rename(columns={"Adj Close": "AdjClose"})
    if "AdjClose" not in df:
        df["AdjClose"] = df["Close"]
    df = df[["Close", "AdjClose", "Volume"]].dropna(subset=["Close"])
    # Drop a trailing zero-volume bar (holiday/placeholder rows Yahoo sometimes adds).
    while len(df) > 1 and (df["Volume"].iloc[-1] == 0 or pd.isna(df["Volume"].iloc[-1])):
        df = df.iloc[:-1]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


# ---------------------------------------------------------------- fundamentals / events
FUND_KEYS = ("long_name", "sector", "industry", "currency", "exchange", "market_cap", "market_cap_basis", "pe", "pe_basis", "pb",
             "div_yield", "high_52w", "low_52w", "target_mean", "rating", "n_analysts", "website",
             "roe", "roa", "op_margin", "fcf", "ocf", "net_income", "fcf_yield", "cash_conversion",
             "revenue_growth", "earnings_growth")


def _pct(x):
    """Yahoo ratio (0.25) -> percent (25.0)."""
    return None if x is None else float(x) * 100


def fundamentals(sym: str) -> dict | None:
    info = retry(lambda: yf.Ticker(sym).info, what=f"info {sym}")
    if not info or not (info.get("marketCap") or info.get("regularMarketPrice")):
        return None
    mcap, mcap_basis = analytics.market_cap(info)
    if mcap is None:
        mcap, mcap_basis = analytics.market_cap(
            info, retry(lambda: float(yf.Ticker(sym).fast_info["market_cap"]), what=f"fast_info {sym}", tries=2))
    pe = info.get("trailingPE")
    pe_basis = "trailing"
    if pe is None and info.get("forwardPE"):
        pe, pe_basis = info.get("forwardPE"), "forward"
    return {
        "long_name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency"),
        "exchange": info.get("exchange"),
        "market_cap": mcap,
        "market_cap_basis": mcap_basis,
        "pe": pe,
        "pe_basis": pe_basis if pe is not None else None,
        "pb": info.get("priceToBook"),
        # yfinance >=0.2.5x reports dividendYield already in percent (e.g. 6.19 = 6.19%)
        "div_yield": info.get("dividendYield"),
        "high_52w": info.get("fiftyTwoWeekHigh"),
        "low_52w": info.get("fiftyTwoWeekLow"),
        "target_mean": info.get("targetMeanPrice"),
        "rating": info.get("recommendationKey") if info.get("recommendationKey") not in (None, "none") else None,
        "n_analysts": info.get("numberOfAnalystOpinions"),
        "website": info.get("website"),
        **analytics.quality_inputs(
            market_cap=mcap, fcf=info.get("freeCashflow"), ocf=info.get("operatingCashflow"),
            net_income=info.get("netIncomeToCommon"), roe=_pct(info.get("returnOnEquity")),
            roa=_pct(info.get("returnOnAssets")), op_margin=_pct(info.get("operatingMargins")),
            revenue_growth=_pct(info.get("revenueGrowth")), earnings_growth=_pct(info.get("earningsGrowth"))),
    }


def earnings(sym: str) -> dict:
    out = {"next_earnings": None, "last_earnings_date": None, "last_surprise_pct": None,
           "last_eps_actual": None, "last_eps_estimate": None}
    today = date.today()
    cal = retry(lambda: yf.Ticker(sym).calendar, what=f"calendar {sym}", tries=2)
    if isinstance(cal, dict):
        dates = [d for d in (cal.get("Earnings Date") or []) if isinstance(d, date)]
        future = sorted(d for d in dates if d >= today)
        if future:
            out["next_earnings"] = future[0].isoformat()
    ed = retry(lambda: yf.Ticker(sym).get_earnings_dates(limit=12), what=f"earnings_dates {sym}", tries=2)
    if isinstance(ed, pd.DataFrame) and not ed.empty:
        ed = ed.sort_index()
        idx_dates = [ts.date() for ts in ed.index]
        if not out["next_earnings"]:
            future = [d for d in idx_dates if d >= today]
            if future:
                out["next_earnings"] = min(future).isoformat()
        reported = ed.dropna(subset=["Reported EPS"]) if "Reported EPS" in ed else pd.DataFrame()
        if not reported.empty:
            row = reported.iloc[-1]
            out["last_earnings_date"] = reported.index[-1].date().isoformat()
            out["last_eps_actual"] = analytics._f(row.get("Reported EPS"))
            out["last_eps_estimate"] = analytics._f(row.get("EPS Estimate"))
            out["last_surprise_pct"] = analytics._f(row.get("Surprise(%)"))
    return out


# ---------------------------------------------------------------- news
def news(sym: str, name: str, query: str | None) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    items = []
    raw = retry(lambda: yf.Ticker(sym).get_news(count=30), what=f"yahoo news {sym}", tries=2) or []
    for n in raw:
        c = n.get("content", n)
        title = c.get("title")
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or c.get("link"))
        pub = c.get("pubDate") or c.get("providerPublishTime")
        try:
            ts = (datetime.fromtimestamp(pub, timezone.utc) if isinstance(pub, (int, float))
                  else datetime.fromisoformat(str(pub).replace("Z", "+00:00")))
        except Exception:  # noqa: BLE001
            ts = None
        if title and url:
            items.append({"title": title, "url": url, "published": ts.isoformat() if ts else None,
                          "publisher": (c.get("provider") or {}).get("displayName") or c.get("publisher"),
                          "_ts": ts})
    source = "Yahoo Finance"
    if not items:
        q = query or f'"{name}"'
        resp = retry(requests.get, "https://news.google.com/rss/search",
                     params={"q": f"{q} when:7d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                     headers=UA, timeout=20, what=f"google news {sym}")
        if resp is not None and resp.ok:
            try:
                for it in ET.fromstring(resp.content).findall(".//item"):
                    try:
                        ts = parsedate_to_datetime(it.findtext("pubDate"))
                    except Exception:  # noqa: BLE001
                        ts = None
                    title = it.findtext("title") or ""
                    publisher = it.findtext("source")
                    if publisher and title.endswith(" - " + publisher):
                        title = title[: -len(publisher) - 3]
                    items.append({"title": title, "url": it.findtext("link"), "publisher": publisher,
                                  "published": ts.isoformat() if ts else None, "_ts": ts})
                source = "Google News RSS"
            except ET.ParseError as exc:
                log.warning("google news parse %s: %s", sym, exc)
    recent = [i for i in items if i["_ts"] is None or i["_ts"] >= cutoff]
    recent.sort(key=lambda i: i["_ts"] or cutoff, reverse=True)
    for i in recent:
        i.pop("_ts", None)
    return {"news": recent[:8], "news_count_7d": len(recent), "news_source": source if items else None}


# ---------------------------------------------------------------- social (US only)
def apewisdom() -> dict[str, dict]:
    data = {}
    page, pages = 1, 1
    while page <= pages and page <= 15:
        r = retry(requests.get, f"https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}",
                  headers=UA, timeout=20, what=f"apewisdom page {page}")
        if r is None or not r.ok:
            break
        js = r.json()
        pages = js.get("pages", 1)
        for row in js.get("results", []):
            data[row["ticker"].upper()] = row
        page += 1
    log.info("ApeWisdom: %d tickers", len(data))
    return data


def stocktwits(sym: str) -> dict | None:
    st_sym = sym.replace("-", ".")
    r = retry(requests.get, f"https://api.stocktwits.com/api/2/streams/symbol/{st_sym}.json",
              headers=UA, timeout=20, what=f"stocktwits {sym}", tries=2)
    if r is None or not r.ok:
        return None
    js = r.json()
    msgs = js.get("messages") or []
    if not msgs:
        return {"stocktwits_watchers": (js.get("symbol") or {}).get("watchlist_count"),
                "stocktwits_msgs_per_day": 0, "stocktwits_bullish": 0, "stocktwits_bearish": 0}
    times = [datetime.fromisoformat(m["created_at"].replace("Z", "+00:00")) for m in msgs]
    span_days = max((datetime.now(timezone.utc) - min(times)).total_seconds() / 86400, 1 / 24)
    senti = [((m.get("entities") or {}).get("sentiment") or {}).get("basic") for m in msgs]
    return {
        "stocktwits_watchers": (js.get("symbol") or {}).get("watchlist_count"),
        # Messages/day estimated from how long the latest 30 messages took to accumulate.
        "stocktwits_msgs_per_day": round(len(msgs) / span_days, 1),
        "stocktwits_bullish": senti.count("Bullish"),
        "stocktwits_bearish": senti.count("Bearish"),
        "stocktwits_sample": len(msgs),
    }


def fx_rates(currencies) -> dict[str, float | None]:
    rates = {"USD": 1.0}
    for c in currencies:
        if c in rates or not c:
            continue
        rates[c] = retry(lambda: float(yf.Ticker(f"{c}USD=X").fast_info["last_price"]), what=f"fx {c}")
    return rates


# ---------------------------------------------------------------- main
def main():
    setup_logging()
    started = datetime.now(timezone.utc)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    previous = {}
    if OUT.exists():
        try:
            previous = {s["symbol"]: s for s in json.loads(OUT.read_text(encoding="utf-8")).get("stocks", [])}
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not read previous stocks.json: %s", exc)

    universe = [(m, st) for m, mk in cfg["markets"].items() for st in mk["stocks"]]
    symbols = [st["symbol"] for _, st in universe]
    log.info("Fetching %d stocks", len(symbols))

    prices = download_prices(symbols)
    ape = apewisdom() if any(m == "US" for m, _ in universe) else {}
    errors = []
    stocks = []
    today = date.today()

    for market, st in universe:
        sym = st["symbol"]
        prev = previous.get(sym)
        rec = {"symbol": sym, "code": st.get("code", sym), "market": market, "name": st.get("name", sym),
               "stale": False, "stale_fields": []}
        for opt in ("bursa_name", "sgx_name"):
            if st.get(opt):
                rec[opt] = st[opt]
        try:
            hist = prices.get(sym)
            if hist is None:
                raise RuntimeError("no price history")
            rec.update(analytics.technicals(hist))

            f = fundamentals(sym)
            if f is None and prev:
                f = {k: prev.get(k) for k in FUND_KEYS}
                rec["stale_fields"].append("fundamentals")
                errors.append({"symbol": sym, "error": "fundamentals unavailable — kept previous values"})
            rec.update(f or {"currency": cfg["markets"][market]["currency"]})
            if rec.get("market_cap") is None and prev and prev.get("market_cap"):
                # Keep yesterday's market cap so the heat-map tile keeps its size.
                rec["market_cap"] = prev["market_cap"]
                rec["market_cap_basis"] = "previous run"
                rec["stale_fields"].append("market_cap")
                errors.append({"symbol": sym, "error": "market cap unavailable — kept previous value"})
            rec["currency"] = rec.get("currency") or cfg["markets"][market]["currency"]
            # Prefer Yahoo's 52w range; fall back to range computed from daily closes.
            rec["high_52w"] = rec.get("high_52w") or rec["high_52w_calc"]
            rec["low_52w"] = rec.get("low_52w") or rec["low_52w_calc"]
            rec["pct_from_high"] = (rec["price"] / rec["high_52w"] - 1) * 100 if rec["high_52w"] else None
            rec["target_upside"] = ((rec["target_mean"] / rec["price"] - 1) * 100
                                    if rec.get("target_mean") and rec.get("price") else None)

            rec.update(earnings(sym))
            ne = rec.get("next_earnings")
            rec["days_to_earnings"] = (date.fromisoformat(ne) - today).days if ne else None

            rec.update(news(sym, st.get("name", sym), st.get("news_query")))

            if market == "US":
                a = ape.get(sym.replace("-", ".").upper()) or ape.get(sym.upper())
                soc = {
                    "reddit_mentions": int(a["mentions"]) if a else 0,
                    "reddit_mentions_24h_ago": int(a["mentions_24h_ago"]) if a and a.get("mentions_24h_ago") else None,
                    "reddit_rank": a.get("rank") if a else None,
                    "reddit_upvotes": int(a["upvotes"]) if a and a.get("upvotes") else None,
                }
                if not ape:  # API down: unknown rather than zero
                    soc = {k: None for k in soc}
                tw = stocktwits(sym)
                soc.update(tw or {"stocktwits_msgs_per_day": None})
                rec["social"] = soc
            else:
                rec["social"] = None  # not available for SG/MY — shown as N/A, never estimated

            rec["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            log.info("OK %-8s %s %.2f  news=%s(%s) earnings=%s", sym, rec["currency"], rec["price"],
                     rec["news_count_7d"], rec["news_source"], rec["next_earnings"])
        except Exception as exc:  # noqa: BLE001
            log.error("FAILED %s: %s", sym, exc)
            errors.append({"symbol": sym, "error": str(exc)})
            if prev:
                rec = dict(prev)
                rec["stale"] = True
                rec["stale_since"] = prev.get("stale_since") or prev.get("last_updated")
            else:
                rec.update({"stale": True, "missing": True, "price": None, "currency": cfg["markets"][market]["currency"]})
        stocks.append(rec)

    # FX for the cross-market heat map
    fx = fx_rates({s.get("currency") for s in stocks})
    for s in stocks:
        rate = fx.get(s.get("currency"))
        s["market_cap_usd"] = s["market_cap"] * rate if s.get("market_cap") and rate else None

    # Factor scores and signals, per market
    for market in cfg["markets"]:
        group = [s for s in stocks if s["market"] == market and not s.get("missing")]
        analytics.score_market(group)
        p80 = analytics.news_p80(group)
        for s in group:
            s["signals"] = analytics.build_signals(s, p80)

    for s in stocks:
        for k in ("high_52w_calc", "low_52w_calc"):
            s.pop(k, None)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_seconds": round((datetime.now(timezone.utc) - started).total_seconds()),
        "markets": {m: {"label": v["label"], "currency": v["currency"]} for m, v in cfg["markets"].items()},
        "fx_to_usd": fx,
        "sources": SOURCES,
        "factor_definitions": {
            "momentum": "Average percentile of 1M/3M/6M return, RSI(14), price vs 50-day MA and price vs 200-day MA",
            "volume": "Percentile of relative volume (latest session volume / prior 20-session average)",
            "value": "Average percentile of earnings yield (1/PE), book yield (1/PB) and dividend yield",
            "attention": "Average percentile of 7-day news count, plus Reddit mentions and StockTwits activity (US only)",
            "reporting": "Average percentile of days to next earnings (sooner = higher) and last EPS surprise %",
            "quality": "Equal-weighted average of three groups: profitability (ROE, ROA, operating margin), "
                       "cash flow (FCF yield, operating cash flow / net income; skipped for banks and insurers) "
                       "and growth (revenue and earnings growth, year on year)",
        },
        "errors": errors,
        "stocks": stocks,
    }
    OUT.write_text(json.dumps(clean(payload), separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for s in stocks if not s.get("stale"))
    log.info("Wrote %s: %d ok, %d stale, %d errors, %ss", OUT.name, ok, len(stocks) - ok, len(errors),
             payload["run_seconds"])
    # Only fail the run (so GitHub flags it) if nothing at all was fetched.
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
