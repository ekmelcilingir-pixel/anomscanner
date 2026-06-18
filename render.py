"""
render.py — fill template/report_shell.html with scan + narrate data.
Produces a self-contained English Anomtara HTML report.
"""

from __future__ import annotations
import os, html, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template", "report_shell.html")

COLOR_WORD = {"r": "RED", "a": "AMBER", "g": "GREEN"}
DOT = {"r": "🔴", "a": "🟡", "g": "🟢"}


def _card_html(c: dict, metric: dict | None) -> str:
    color = c.get("color", "a")
    tk = c["ticker"]
    pill = html.escape(c.get("pill_label", "WATCH"))
    minipills = ""
    if metric:
        dirn = "1.5× direction (threat)" if metric["direction"] == 1.5 else "1.0× direction"
        pos = (f"Position: {int(metric['shares']):,} × ${metric['close']} "
               f"≈ ${metric['pos_value']:,}") if metric["is_holding"] else "Watchlist (no position)"
        minipills = (
            f'<span class="minipill">Anomaly <strong>{metric["anomaly"]}</strong>/100 · {DOT[color]} Caution</span>'
            f'<span class="minipill">Impact <strong>{metric["impact"]}</strong> · '
            f'<strong>{metric["weight_pct"]}%</strong> weight × {dirn}</span>'
            f'<span class="minipill">{html.escape(pos)}</span>'
        )
        opt = metric.get("options") or {}
        opt_bits = []
        if opt.get("iv_pct") is not None:
            opt_bits.append(f'IV <strong>{opt["iv_pct"]}%</strong>')
        if opt.get("net_gex") is not None:
            opt_bits.append(f'GEX <strong>{opt["net_gex"]:,}</strong>')
        if opt.get("pc_ratio") is not None:
            opt_bits.append(f'P/C <strong>{opt["pc_ratio"]}</strong>')
        if opt.get("max_pain") is not None:
            opt_bits.append(f'MaxPain <strong>${opt["max_pain"]}</strong>')
        if opt_bits:
            minipills += f'<span class="minipill">{" · ".join(opt_bits)}</span>'
    ev = "".join(f'<div class="kanit">{e}</div>' for e in c.get("evidence", []))
    news = c.get("news_sources", [])
    if news:
        links = " · ".join(
            f'<a href="{html.escape(n.get("url",""))}" target="_blank">{html.escape(n.get("title",""))}</a>'
            for n in news)
        ev += f'<div class="kanit"><strong>News sources:</strong> {links}</div>'
    return f'''<div class="card {color}">
  <div class="top">
    <span class="tk">{tk}</span>
    <span class="co">{html.escape(c.get("subtitle", metric["sector_etf"] if metric else ""))}</span>
    <span class="pill {color}">{pill}</span>
  </div>
  <div class="pillrow">{minipills}</div>
  <div class="row"><div class="lbl">Situation:</div><div class="val">{c.get("situation","")}</div></div>
  <div class="row"><div class="lbl">What to do:</div><div class="val">{c.get("what_to_do","")}</div></div>
  <div class="detay">
    <strong>Evidence (DATA → EVENT → IMPACT):</strong>
    {ev}
  </div>
</div>'''


def render(scan: dict, narr: dict, date: datetime.date, market: str = "") -> str:
    shell = open(TEMPLATE, encoding="utf-8").read()
    by_ticker = {r["ticker"]: r for r in scan["flagged"]}

    long_date = date.strftime("%B %-d, %Y") if os.name != "nt" else date.strftime("%B %d, %Y")
    weekday = date.strftime("%A")
    t_close = (date - datetime.timedelta(days=1)).strftime("%b %-d, %Y") if os.name != "nt" \
        else (date - datetime.timedelta(days=1)).strftime("%b %d, %Y")

    meta = f"{long_date} · {weekday} · T = {t_close} close"
    scope = (f"Universe scanned: {scan['n_scanned']} names "
             f"({scan['n_holdings']} open positions) · Source: Yahoo Finance (price/volume/trend)"
             f"{' + options layer' if os.environ.get('UW_API_KEY') else ' · options layer not configured'}")

    cards = "\n".join(_card_html(c, by_ticker.get(c["ticker"])) for c in narr.get("cards", [])) \
        or '<div class="no-anom">No above-threshold anomalies today.</div>'

    flagged_line = " · ".join(f'{r["ticker"]} ({r["anomaly"]})' for r in scan["flagged"]) or "none"
    foot = (f'<strong>Above threshold today:</strong> {flagged_line}<br>'
            f'<strong>Data status:</strong> Yahoo Finance price/volume/technicals pulled for all '
            f'{scan["n_scanned"]} names. All numbers from the T close. '
            f'Options/derivatives layer (gamma/IV/flow/max pain) '
            f'{"included via Unusual Whales." if os.environ.get("UW_API_KEY") else "not configured (price/volume/trend only)."}')

    out = (shell
           .replace("{{REPORT_DATE_LONG}}", html.escape(long_date))
           .replace("{{META}}", html.escape(meta))
           .replace("{{SCOPE}}", html.escape(scope))
           .replace("{{SUMMARY}}", narr.get("summary", ""))
           .replace("{{CARDS}}", cards)
           .replace("{{BELOW_THRESHOLD}}", narr.get("below_text", ""))
           .replace("{{FOOT}}", foot))
    return out
