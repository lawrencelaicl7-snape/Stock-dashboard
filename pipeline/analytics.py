"""Indicators, factor scores and rule-based signals.

All factor scores are percentiles (0-100) computed *within each market*, so a US
stock is only compared with other US stocks. The weighted "focus score" is
computed in the browser so the weights can be changed with sliders.
"""
import math

import numpy as np
import pandas as pd

FACTORS = ["momentum", "volume", "value", "attention", "reporting"]


# ---------------------------------------------------------------- indicators
def rsi(close: pd.Series, period: int = 14) -> float | None:
    """Wilder's RSI on the last bar."""
    delta = close.diff().dropna()
    if len(delta) < period + 1:
        return None
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_loss = loss.iloc[-1]
    if last_loss == 0:
        return 100.0
    return float(100 - 100 / (1 + gain.iloc[-1] / last_loss))


def pct_return(series: pd.Series, sessions: int) -> float | None:
    if len(series) <= sessions:
        return None
    start = series.iloc[-1 - sessions]
    return None if not start else float(series.iloc[-1] / start - 1) * 100


def crossed(close: pd.Series, ma: pd.Series, lookback: int = 5) -> str | None:
    """'above'/'below' if price crossed the moving average in the last N sessions."""
    diff = (close - ma).dropna()
    if len(diff) < lookback + 1:
        return None
    now = diff.iloc[-1] > 0
    before = diff.iloc[-1 - lookback] > 0
    if now and not before:
        return "above"
    if before and not now:
        return "below"
    return None


def technicals(hist: pd.DataFrame) -> dict:
    """hist: DataFrame with Close, AdjClose, Volume indexed by date (>= ~1y)."""
    close, adj, vol = hist["Close"], hist["AdjClose"], hist["Volume"]
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    prev20 = vol.iloc[-21:-1]
    avg20 = float(prev20.mean()) if len(prev20) == 20 else None
    last = float(close.iloc[-1])
    out = {
        "price": last,
        "prev_close": float(close.iloc[-2]) if len(close) > 1 else None,
        "change_1d": pct_return(adj, 1),
        "change_1w": pct_return(adj, 5),
        "change_1m": pct_return(adj, 21),
        "change_3m": pct_return(adj, 63),
        "change_6m": pct_return(adj, 126),
        "change_1y": pct_return(adj, 252),
        "rsi14": rsi(close),
        "ma50": _f(ma50.iloc[-1]),
        "ma200": _f(ma200.iloc[-1]),
        "volume": _f(vol.iloc[-1]),
        "avg_volume_20d": avg20,
        "rel_volume": (float(vol.iloc[-1]) / avg20) if avg20 else None,
        "last_trade_date": hist.index[-1].strftime("%Y-%m-%d"),
        "cross_ma50": crossed(close, ma50),
        "cross_ma200": crossed(close, ma200),
        "cross_50_200": crossed(ma50, ma200, 10),  # golden / death cross
    }
    last_year = close.iloc[-252:]
    out["high_52w_calc"] = float(last_year.max())
    out["low_52w_calc"] = float(last_year.min())
    out["new_52w_high"] = bool(last >= last_year.max())
    out["price_vs_ma50"] = (last / out["ma50"] - 1) * 100 if out["ma50"] else None
    out["price_vs_ma200"] = (last / out["ma200"] - 1) * 100 if out["ma200"] else None

    # Compact 1-year chart history (MAs use the full 2y window so MA200 exists from day 1).
    tail = hist.index[-252:]
    out["history"] = {
        "dates": [d.strftime("%Y-%m-%d") for d in tail],
        "close": [_r(x) for x in close.loc[tail]],
        "volume": [int(x) if not math.isnan(x) else None for x in vol.loc[tail]],
        "ma50": [_r(x) for x in ma50.loc[tail]],
        "ma200": [_r(x) for x in ma200.loc[tail]],
    }
    return out


def _f(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def _r(x):
    x = _f(x)
    if x is None:
        return None
    return round(x, 4 if abs(x) < 10 else 2)


# ---------------------------------------------------------------- scoring
def percentile(values: list, higher_is_better: bool = True) -> list:
    """Percentile rank 0-100 among non-missing values (ties averaged); None stays None."""
    s = pd.Series(values, dtype="float64")
    if not higher_is_better:
        s = -s
    valid = s.dropna()
    if len(valid) == 0:
        return [None] * len(values)
    if len(valid) == 1:
        ranks = pd.Series(50.0, index=valid.index)
    else:
        ranks = (valid.rank(method="average") - 1) / (len(valid) - 1) * 100
    return [None if i not in ranks.index else float(ranks[i]) for i in range(len(values))]


def _mean_components(rows: list[list]) -> list:
    """Average component percentiles per stock, skipping missing components."""
    out = []
    for parts in zip(*rows):
        vals = [p for p in parts if p is not None]
        out.append(round(sum(vals) / len(vals), 1) if vals else None)
    return out


def score_market(stocks: list[dict]) -> None:
    """Adds stock['factors'] and stock['factor_components'] in place."""
    def col(key):
        return [s.get(key) for s in stocks]

    def inv(key):  # earnings yield / book yield: higher = cheaper; negative earnings rank last
        out = []
        for v in col(key):
            out.append(None if v is None else (1 / v if v > 0 else -1))
        return out

    comp = {
        "momentum": {
            "1M return": percentile(col("change_1m")),
            "3M return": percentile(col("change_3m")),
            "6M return": percentile(col("change_6m")),
            "RSI(14)": percentile(col("rsi14")),
            "Price vs 50-day MA": percentile(col("price_vs_ma50")),
            "Price vs 200-day MA": percentile(col("price_vs_ma200")),
        },
        "volume": {"Relative volume": percentile(col("rel_volume"))},
        "value": {
            "Earnings yield (1/PE)": percentile(inv("pe")),
            "Book yield (1/PB)": percentile(inv("pb")),
            "Dividend yield": percentile(col("div_yield")),
        },
        "attention": {
            "News count (7d)": percentile(col("news_count_7d")),
            "Reddit mentions": percentile([_log(s.get("social", {}) or {}, "reddit_mentions") for s in stocks]),
            "StockTwits messages/day": percentile([_log(s.get("social", {}) or {}, "stocktwits_msgs_per_day") for s in stocks]),
        },
        "reporting": {
            # sooner earnings = higher score; unknown or past dates rank as furthest away
            "Days to earnings": percentile(
                [d if d is not None and d >= 0 else 999 for d in col("days_to_earnings")], higher_is_better=False),
            "Last EPS surprise": percentile(col("last_surprise_pct")),
        },
    }
    scores = {f: _mean_components(list(c.values())) for f, c in comp.items()}
    for i, s in enumerate(stocks):
        s["factors"] = {f: scores[f][i] for f in FACTORS}
        s["factor_components"] = {
            f: {name: (None if v[i] is None else round(v[i], 1)) for name, v in c.items()}
            for f, c in comp.items()
        }


def _log(d, key):
    v = d.get(key)
    return None if v is None else math.log1p(v)


# ---------------------------------------------------------------- signals
def build_signals(s: dict, market_news_p80: float | None) -> list[dict]:
    """Plain-English, rule-based reasons. 'weight' orders them; 'kind' drives the icon colour."""
    sig = []

    def add(text, kind, weight, metric=None):
        sig.append({"text": text, "kind": kind, "weight": weight, "metric": metric})

    rv = s.get("rel_volume")
    if rv is not None and rv >= 1.5:
        add(f"Volume {rv:.1f}x its 20-day average", "volume", 3 + min(rv, 5), f"rel vol {rv:.2f}")
    d = s.get("days_to_earnings")
    if d is not None and 0 <= d <= 14:
        when = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d} days")
        add(f"Earnings {when} ({s['next_earnings']})", "event", 8 - d / 2, f"{d} days")
    for key, label in (("cross_ma200", "200-day"), ("cross_ma50", "50-day")):
        c = s.get(key)
        ma = s.get("ma200" if label == "200-day" else "ma50")
        if c and ma:
            add(f"Crossed {c} {label} MA ({_fmt(ma)})", "bull" if c == "above" else "bear",
                6 if label == "200-day" else 4, f"MA {_fmt(ma)}")
    c = s.get("cross_50_200")
    if c:
        add("Golden cross: 50-day MA moved above 200-day" if c == "above"
            else "Death cross: 50-day MA moved below 200-day", "bull" if c == "above" else "bear", 5)
    hi, lo, px = s.get("high_52w"), s.get("low_52w"), s.get("price")
    if s.get("new_52w_high"):
        add(f"New 52-week high ({_fmt(px)})", "bull", 6, f"high {_fmt(hi)}")
    elif hi and px and px >= hi * 0.97:
        add(f"Near 52-week high ({(1 - px / hi) * 100:.1f}% below {_fmt(hi)})", "bull", 4, f"high {_fmt(hi)}")
    if lo and px and px <= lo * 1.05:
        add(f"Near 52-week low ({(px / lo - 1) * 100:.1f}% above {_fmt(lo)})", "bear", 4, f"low {_fmt(lo)}")
    r = s.get("rsi14")
    if r is not None and r >= 70:
        add(f"RSI {r:.0f} — overbought zone (>70)", "warn", 3, f"RSI {r:.1f}")
    elif r is not None and r <= 30:
        add(f"RSI {r:.0f} — oversold zone (<30)", "warn", 3, f"RSI {r:.1f}")
    c1 = s.get("change_1d")
    if c1 is not None and abs(c1) >= 3:
        add(f"{'Up' if c1 > 0 else 'Down'} {abs(c1):.1f}% today", "bull" if c1 > 0 else "bear", 3 + abs(c1) / 2)
    m1 = s.get("change_1m")
    if m1 is not None and abs(m1) >= 10:
        add(f"{'Up' if m1 > 0 else 'Down'} {abs(m1):.1f}% over 1 month", "bull" if m1 > 0 else "bear", 2 + abs(m1) / 10)
    soc = s.get("social") or {}
    m, m0 = soc.get("reddit_mentions"), soc.get("reddit_mentions_24h_ago")
    if m is not None and m0 and m >= 10 and m >= m0 * 2:
        add(f"Reddit mentions up {(m / m0 - 1) * 100:.0f}% ({m0} → {m} in 24h)", "attention", 5)
    elif m is not None and m >= 50:
        add(f"{m} Reddit mentions in 24h (rank #{soc.get('reddit_rank')})", "attention", 3)
    nc = s.get("news_count_7d")
    if nc and market_news_p80 is not None and nc >= max(market_news_p80, 10):
        add(f"{'100+' if nc >= 100 else nc} news headlines in the last 7 days", "attention", 2.5)
    sp = s.get("last_surprise_pct")
    if sp is not None and abs(sp) >= 5:
        add(f"{'Beat' if sp > 0 else 'Missed'} EPS estimate by {abs(sp):.1f}% last quarter",
            "bull" if sp > 0 else "bear", 2 + min(abs(sp), 30) / 10)
    up = s.get("target_upside")
    if up is not None and abs(up) >= 15 and (s.get("n_analysts") or 0) >= 3:
        add(f"Analyst mean target {abs(up):.0f}% {'above' if up > 0 else 'below'} price ({s['n_analysts']} analysts)",
            "info", 2)
    dy = s.get("div_yield")
    if dy is not None and dy >= 6:
        add(f"Dividend yield {dy:.1f}%", "info", 1.5)
    sig.sort(key=lambda x: -x["weight"])
    for x in sig:
        x["weight"] = round(x["weight"], 2)
    return sig


def _fmt(x):
    if x is None:
        return "-"
    return f"{x:,.2f}" if x < 1000 else f"{x:,.0f}"


def news_p80(stocks):
    vals = [s.get("news_count_7d") for s in stocks if s.get("news_count_7d") is not None]
    return float(np.percentile(vals, 80)) if vals else None
