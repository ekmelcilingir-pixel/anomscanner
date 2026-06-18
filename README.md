# Anomscanner — daily portfolio anomaly report

A self-updating daily anomaly scanner for your equity portfolio + watchlist, in
English. Mirrors the GitHub Actions → data → Claude API → GitHub Pages pattern:
each run writes a dated HTML report and prepends it to `manifest.json`; the
platform page (`docs/index.html`) reads the manifest and the **date dropdown
fills automatically** — newest first.

```
holdings.csv ─┐
watchlist.csv ─┼─▶ scan.py  ──▶ narrate.py ──▶ render.py ──▶ docs/reports/anomtara-YYYY-MM-DD-*.html
              │   (Yahoo:        (Claude API:    (English      docs/manifest.json  ◀── dropdown reads this
              │    z-scores,      English         HTML from
              │    vol, SMA,      narrative)      template)
              │    gap/ATR)
GitHub Actions (weekday cron) runs main.py ──▶ commits to /docs ──▶ GitHub Pages serves index.html
```

## What it scans (free layer)
Per name, from Yahoo Finance daily OHLCV:
- `z_abs` — return vs the stock's own ~60-day normal
- `z_rel` — return relative to its **sector ETF**, z-scored (separates stock-specific from sector moves)
- `vol_ratio` — today's volume / 20-day average
- `SMA50 / SMA200`, below-50d flag, death-cross flag
- `gap/ATR(14)`, 52-week range position

**Anomaly Score (0-100)** = price-z (30) + relative-z (25) + volume (20) + trend breaks (20) + gap (5).
Names ≥ 40 are reported; the rest go to the below-threshold list.

**Portfolio Impact Score** = `round(Anomaly × weight% × direction / 10)`,
direction = 1.5 when a bearish move threatens a long position, else 1.0
(same formula the reference report used).

## Quick start (local)
```bash
pip install -r requirements.txt
# edit holdings.csv with your real positions (ticker,shares,cost,sector_etf)
export ANTHROPIC_API_KEY=sk-ant-...     # optional but recommended; without it a plain fallback narrative is used
python main.py
open docs/index.html                     # use a local server for the dropdown to fetch the manifest:
# python -m http.server -d docs 8080  →  http://localhost:8080
```

## Deploy (auto-updating)
1. Create a **public** GitHub repo, push these files.
2. Settings → Pages → Source = **Deploy from a branch**, branch = `main`, folder = **/docs**.
   Your platform will live at `https://<user>.github.io/<repo>/`.
3. Settings → Secrets and variables → Actions → add `ANTHROPIC_API_KEY`
   (and later `UW_API_KEY` for the options layer).
4. The workflow (`.github/workflows/anomtara.yml`) runs weekdays 11:30 UTC and on the
   manual **Run workflow** button. Each run commits a new report + manifest entry, and
   the dropdown picks it up automatically.

## Customize
- **Positions:** `holdings.csv` (`ticker,shares,cost,sector_etf`). `sector_etf` is what
  `z_rel` is measured against (e.g. SMH for semis, XLK tech, XLV health, XLE energy, XLY discretionary).
- **Watchlist:** `watchlist.csv` (`ticker,sector_etf`) — scanned but no position weight.
- **Threshold / weights:** top of `scan.py` (`THRESHOLD`, the component caps).
- **Model:** `ANOMTARA_MODEL` env (default `claude-sonnet-4-6`; use `claude-opus-4-8` for richer prose).

## Options / derivatives layer (Unusual Whales, paid)
Built in `fetch_options.py`. When `UW_API_KEY` (or `UW_API_TOKEN`) is set, the
pipeline enriches **only the flagged names** (to conserve API calls) with:

| signal            | endpoint                                   |
|-------------------|--------------------------------------------|
| ATM IV + rank     | `/api/stock/{t}/interpolated-iv`           |
| Net GEX (gamma)   | `/api/stock/{t}/spot-exposures/strike`     |
| Put/Call + volume | `/api/stock/{t}/options-volume`            |
| Unusual flow      | `/api/option-trades/flow-alerts`           |
| Dark-pool prints  | `/api/darkpool/{t}`                        |

Auth is `Authorization: Bearer <token>` + `UW-CLIENT-API-ID: 100001`, all GET.
These metrics flow into the narrative (IV/GEX/flow/dark-pool evidence) and into a
per-card minipill (IV · GEX · P/C). Each metric is fetched in its own try/except and
tolerant of field renames, so an endpoint change degrades one line, not the report.
Without the key the report runs cleanly on the price/volume/trend layer only. 

**Max pain** is derived from open interest by strike (`/api/stock/{t}/option-contracts`):
the nearest-expiry strike that minimises total payout to option holders — shown in the
evidence and the card minipill. **Watchlist names** that stay below threshold still get a
*light* options pass (IV · P/C · flow tilt, 3 calls each) so one-sided flow or elevated IV
surfaces as an early warning in the below-threshold paragraph.

Get a key at unusualwhales.com/settings/api-dashboard, then add it as the GitHub
secret `UW_API_KEY` (the workflow already passes it through).

## Clean start
The repo ships with one demo report so Pages isn't empty. For a clean slate:
`rm docs/reports/*.html && echo "[]" > docs/manifest.json`, then run `main.py`.

> Not investment advice. An anomaly is a statistically unusual move that "deserves a
> manual look", never a certainty.
