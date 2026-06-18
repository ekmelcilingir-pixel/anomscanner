"""
narrate.py — turn the scanner's flagged anomalies into English narrative.

Calls the Anthropic API once and returns structured JSON:
  summary       one-sentence portfolio summary (English)
  tone          short risk tone phrase
  cards[]       per ticker: pill_label, color (r/a/g), situation, what_to_do,
                evidence[] (strings), news_sources[] ({title,url})
  below_text    one paragraph for the below-threshold names

If ANTHROPIC_API_KEY is missing, falls back to a plain templated narrative so the
pipeline still produces a valid (if less polished) English report.
"""

from __future__ import annotations
import os, json
import fetch_options

MODEL = os.environ.get("ANOMTARA_MODEL", "claude-sonnet-4-6")

SYSTEM = """You are Anomscanner, an equity-portfolio anomaly analyst.
You receive structured scan metrics for flagged names and must write a calm,
precise daily report.

ABSOLUTE RULES:
- Write the ENTIRE output in ENGLISH. Never output Turkish. Every label, phrase,
  action and sentence must be English.
- Output ONLY valid JSON, no markdown, no preamble.
- An anomaly is a statistically unusual move, NOT a certainty or "manipulation".
  Never give investment advice; frame everything as "deserves a manual look".
- Tie every claim to the data you were given (DATA -> EVENT -> IMPACT). Do not
  invent numbers, news, or option data that is not in the input.
- pill colors: "r" = real risk (tighten stop / trim), "a" = watch / no add yet,
  "g" = healthy / hold. Choose based on direction and severity.
- Keep each field tight and concrete. Evidence items start with the metric."""

PROMPT = """Scan date (T close): {date}
Market context: {market}

Flagged names (JSON). Some include an "options" object (UW: iv_pct, iv_rank,
net_gex, gex_sign, pc_ratio, call_prem, put_prem, flow_tilt, top_flow, darkpool_top).
When "options" is present, weave gamma/IV/flow/dark-pool into the evidence and let it
inform the pill color. When it is absent, say options data is unavailable and rely on
price/volume/trend only.
{flagged}

Below-threshold names (JSON). Watchlist names may carry "options_light"
(iv_pct, pc_ratio, flow_tilt) — if any show notable IV or one-sided flow,
mention them in below_text as early-warning watchlist signals.
{below}

Return JSON with this exact shape:
{{
  "summary": "one sentence, English, name the key risks",
  "tone": "short phrase e.g. 'risk-weighted'",
  "cards": [
    {{
      "ticker": "MU",
      "pill_label": "WATCH · NO ADD",
      "color": "a",
      "situation": "1-2 sentences: what happened",
      "what_to_do": "1-2 sentences: rule-based action, may reference porttech/protect/tradesetups/trendcheck skills",
      "evidence": ["• <b>metric</b> ... DATA->EVENT->IMPACT", "..."],
      "news_sources": []
    }}
  ],
  "below_text": "one short paragraph covering the below-threshold names with their % move and why each was skipped"
}}
Produce one card per flagged name, in the same order as given."""


def _fallback(scan: dict, date: str, market: str) -> dict:
    cards = []
    for r in scan["flagged"]:
        color = "r" if (r["direction"] == 1.5 and r["impact"] >= 15) else "a"
        cards.append({
            "ticker": r["ticker"],
            "pill_label": "TIGHTEN STOP · NO ADD" if color == "r" else "WATCH · NO ADD",
            "color": color,
            "situation": (f"{r['ticker']} moved {r['today_ret_pct']}% today "
                          f"(z_abs {r['z_abs']}, z_rel {r['z_rel']} vs {r['sector_etf']}), "
                          f"volume {r['vol_ratio']}× the 20-day average."),
            "what_to_do": ("Do not add. Manage the stop with discipline and confirm a trigger "
                           "before any change. Run trendcheck for trend quality, protect for hedging."),
            "evidence": [
                f"• <b>z_abs {r['z_abs']} · z_rel {r['z_rel']} vs {r['sector_etf']}</b> — stock vs sector move.",
                f"• <b>Volume {r['vol_ratio']}×</b> 20-day average.",
                (f"• <b>Trend:</b> close {r['close']} vs SMA50 {r['sma50']} / SMA200 {r['sma200']}"
                 f"{' — below 50d' if r['below_sma50'] else ''}{' — death cross' if r['death_cross'] else ''}."),
                f"• <b>52w range:</b> low {r['lo52']} / high {r['hi52']}.",
            ] + fetch_options.options_evidence(r.get("options")),
            "news_sources": [],
        })
    below = ", ".join(f"{r['ticker']} ({r['today_ret_pct']:+}%)" for r in scan["below"][:8])
    wl_notes = []
    for r in scan["below"]:
        o = r.get("options_light") or {}
        if o.get("iv_pct") or o.get("flow_tilt"):
            bits = []
            if o.get("iv_pct"): bits.append(f"IV ~{o['iv_pct']}%")
            if o.get("flow_tilt"): bits.append(o["flow_tilt"])
            if o.get("pc_ratio") is not None: bits.append(f"P/C {o['pc_ratio']}")
            wl_notes.append(f"{r['ticker']} ({', '.join(bits)})")
    wl_line = (" Watchlist options watch: " + "; ".join(wl_notes) + ".") if wl_notes else ""
    return {
        "summary": (f"Today {len(scan['flagged'])} name(s) showed above-threshold anomalies "
                    f"in the portfolio. {market}"),
        "tone": "risk-weighted",
        "cards": cards,
        "below_text": ((f"These names showed movement but scored below the {scan['threshold']} "
                        f"threshold: {below}." + wl_line) if below else "No below-threshold names today."),
    }


def narrate(scan: dict, date: str, market: str = "") -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not scan["flagged"]:
        return _fallback(scan, date, market)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": PROMPT.format(
                date=date, market=market or "(none provided)",
                flagged=json.dumps(scan["flagged"], indent=2),
                below=json.dumps(scan["below"][:10], indent=2),
            )}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(text)
        # basic shape guard
        assert "cards" in data and "summary" in data
        return data
    except Exception as e:
        print(f"[narrate] API/parse failed ({e}); using fallback.")
        return _fallback(scan, date, market)
