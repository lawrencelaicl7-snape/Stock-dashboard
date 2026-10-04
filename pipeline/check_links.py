"""Check the research-link patterns used in the detail panel.

    python pipeline/check_links.py            # all stocks
    python pipeline/check_links.py --sample   # first 3 stocks per market

Builds the same URLs as links() in assets/app.js (keep the two in sync), fetches
each one and reports the HTTP status, final URL and page title so you can see
whether the page is about the right company. Some sites (notably Bloomberg)
refuse automated requests; those show as "blocked" and need a manual check in a
browser. Writes a Markdown table to data/link_check.md (and to the GitHub Actions
job summary when run there). Never fails the build.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "stocks.json"
OUT = ROOT / "data" / "link_check.md"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}


def links(s: dict) -> list[tuple[str, str]]:
    """Mirror of links() in assets/app.js."""
    sym, code = s["symbol"], s.get("code") or s["symbol"]
    y = f"https://finance.yahoo.com/quote/{quote(sym)}/"
    if s["market"] == "US":
        dot = sym.lower().replace("-", ".", 1)
        exch = "xnas" if s.get("exchange") in ("NMS", "NGM", "NCM", "NAS") else "xnys"
        return [("Yahoo Finance", y),
                ("StockAnalysis", f"https://stockanalysis.com/stocks/{dot}/"),
                ("Finviz", f"https://finviz.com/quote.ashx?t={quote(sym)}"),
                ("Morningstar", f"https://www.morningstar.com/stocks/{exch}/{dot}/quote"),
                ("Bloomberg", f"https://www.bloomberg.com/quote/{sym.replace('-', '/', 1)}:US")]
    if s["market"] == "SG":
        sgx_name = s.get("sgx_name") or (s.get("long_name") or s["name"]).upper()
        return [("Yahoo Finance", y),
                ("StockAnalysis", f"https://stockanalysis.com/quote/sgx/{code}/"),
                ("Morningstar", f"https://www.morningstar.com/stocks/xses/{code.lower()}/quote"),
                ("Bloomberg", f"https://www.bloomberg.com/quote/{code}:SP"),
                ("SGX announcements", "https://www.sgx.com/securities/company-announcements"
                                      f"?value={quote(sgx_name)}&type=company"),
                ("The Business Times", f"https://www.businesstimes.com.sg/search?query={quote(s['name'])}")]
    out = [("Yahoo Finance", y)]
    if s.get("bursa_name"):
        out.append(("StockAnalysis", f"https://stockanalysis.com/quote/klse/{quote(s['bursa_name'])}/"))
    out += [("Morningstar", f"https://www.morningstar.com/stocks/xkls/{code}/quote"),
            ("Bloomberg", f"https://www.bloomberg.com/quote/{code}:MK"),
            ("Bursa announcements", "https://www.bursamalaysia.com/market_information/announcements/"
                                    f"company_announcement?company={code}"),
            ("The Edge Malaysia", f"https://theedgemalaysia.com/askedge/klse/{code}")]
    return out


def check(url: str) -> tuple[str, str, str]:
    try:
        r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return "error", "", type(exc).__name__
    m = re.search(r"<title[^>]*>(.*?)</title>", r.text[:300_000], re.S | re.I)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1)).strip())[:90] if m else ""
    status = str(r.status_code)
    if r.status_code in (401, 403, 429) or "captcha" in title.lower() or "robot" in title.lower():
        status += " blocked"
    return status, r.url, title


def main():
    stocks = json.loads(DATA.read_text(encoding="utf-8"))["stocks"]
    if "--sample" in sys.argv:
        stocks = [s for m in ("US", "SG", "MY") for s in [x for x in stocks if x["market"] == m][:3]]
    rows, summary = [], {}
    for s in stocks:
        for site, url in links(s):
            status, final, title = check(url)
            ok = status.startswith("2")
            key = (s["market"], site)
            summary.setdefault(key, [0, 0])
            summary[key][0] += ok
            summary[key][1] += 1
            rows.append(f"| {s['market']} | {s['symbol']} | {site} | {status} | {title.replace('|', '/')} | "
                        f"[link]({url}){' → redirected' if final.rstrip('/') != url.rstrip('/') else ''} |")
            print(f"{s['symbol']:9} {site:20} {status:12} {title[:70]}")
            time.sleep(0.4)
    lines = ["# Research link check", "", "## Summary (HTTP 2xx / checked)", "",
             "| Market | Site | OK |", "|---|---|---|"]
    lines += [f"| {m} | {site} | {ok}/{n} |" for (m, site), (ok, n) in summary.items()]
    lines += ["", "## Detail (check the title names the right company)", "",
              "| Mkt | Symbol | Site | Status | Page title | URL |", "|---|---|---|---|---|---|", *rows]
    text = "\n".join(lines) + "\n"
    OUT.write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
