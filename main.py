"""
main.py — daily Anomtara pipeline entrypoint.

  1. load holdings.csv (+ optional watchlist.csv)
  2. scan price/volume/trend anomalies (scan.py)
  3. write English narrative (narrate.py, Claude API)
  4. render the English HTML report (render.py)
  5. write docs/reports/anomtara-YYYY-MM-DD-<ts>.html
  6. prepend an entry to docs/manifest.json  (the platform's dropdown reads this)

Run locally:   python main.py
In CI:          set ANTHROPIC_API_KEY (and optionally UW_API_KEY) as secrets.
"""

from __future__ import annotations
import os, csv, json, time, datetime

import scan as scanner
import narrate as narrator
import render as renderer
import fetch_options

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
REPORTS = os.path.join(DOCS, "reports")
MANIFEST = os.path.join(DOCS, "manifest.json")


def _load(path):
    p = os.path.join(HERE, path)
    if not os.path.exists(p):
        return []
    with open(p, newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("ticker")]


def _market_context():
    """One-line market context from SPY's last close."""
    try:
        import yfinance as yf
        spy = yf.download("SPY", period="5d", interval="1d", progress=False, auto_adjust=False)
        chg = (spy["Close"].iloc[-1] / spy["Close"].iloc[-2] - 1) * 100
        chg = float(chg)
        word = "down day" if chg < -0.3 else ("up day" if chg > 0.3 else "flat day")
        return f"Broad market {word} (S&P {chg:+.2f}%)."
    except Exception:
        return ""


def main():
    os.makedirs(REPORTS, exist_ok=True)

    holdings = [{"ticker": r["ticker"], "shares": r.get("shares"),
                 "cost": r.get("cost"), "sector_etf": r.get("sector_etf", "SPY")}
                for r in _load("holdings.csv")]
    watchlist = [{"ticker": r["ticker"], "sector_etf": r.get("sector_etf", "SPY")}
                 for r in _load("watchlist.csv")]

    if not holdings and not watchlist:
        raise SystemExit("No tickers found. Add at least holdings.csv (ticker,shares,cost,sector_etf).")

    market = _market_context()
    print(f"Scanning {len(holdings)} holdings + {len(watchlist)} watchlist names …")
    scan = scanner.scan(holdings, watchlist)
    print(f"  flagged: {[r['ticker'] for r in scan['flagged']]}")

    if fetch_options._token() and (scan["flagged"] or scan["below"]):
        watch_light = [r for r in scan["below"] if not r["is_holding"]][:6]
        print(f"  Unusual Whales: full {len(scan['flagged'])} flagged + "
              f"light {len(watch_light)} watchlist …")
        fetch_options.enrich(scan["flagged"], watch_light)

    today = datetime.date.today()
    narr = narrator.narrate(scan, today.isoformat(), market)
    html_out = renderer.render(scan, narr, today, market)

    ts = int(time.time() * 1000)
    fname = f"anomscanner-{today.isoformat()}-{ts}.html"
    with open(os.path.join(REPORTS, fname), "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"  wrote reports/{fname}")

    # update manifest (newest first)
    manifest = []
    if os.path.exists(MANIFEST):
        try:
            manifest = json.load(open(MANIFEST))
        except Exception:
            manifest = []
    manifest.insert(0, {
        "filename": fname,
        "for_date": today.isoformat(),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "flagged": [r["ticker"] for r in scan["flagged"]],
    })
    json.dump(manifest, open(MANIFEST, "w"), indent=2)
    print(f"  manifest now has {len(manifest)} reports")


if __name__ == "__main__":
    main()
