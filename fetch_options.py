"""
fetch_options.py — Unusual Whales options/derivatives layer for Anomscanner.

Adds, per flagged ticker, the gamma/IV/flow/dark-pool context the price-only
layer can't see. Only called for flagged names (to conserve API calls), exactly
like the reference report ("UW deepened for priority names").

API facts (verified against unusualwhales.com/skill.md):
  Base   : https://api.unusualwhales.com
  Auth   : Authorization: Bearer <token>   AND   UW-CLIENT-API-ID: 100001
  Method : GET only
  Endpoints used:
    /api/stock/{t}/interpolated-iv        -> ATM IV + percentile/rank
    /api/stock/{t}/spot-exposures/strike  -> spot GEX by strike (net gamma)
    /api/stock/{t}/options-volume         -> call/put volume, put/call ratio
    /api/option-trades/flow-alerts        -> notable unusual flow (call/put premium)
    /api/darkpool/{t}                      -> recent dark-pool prints

Every metric is fetched in its own try/except and tolerant of missing/renamed
fields, so a single endpoint change degrades one line, never the whole report.
Set UW_API_KEY (or UW_API_TOKEN). Without it this module is never called.
"""

from __future__ import annotations
import os, time

BASE = "https://api.unusualwhales.com"


def _token() -> str | None:
    for k in ("UW_API_KEY", "UW_API_TOKEN", "UNUSUAL_WHALES_API_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v
    return None


def _client():
    import httpx
    tok = _token()
    if not tok:
        return None
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {tok}", "UW-CLIENT-API-ID": "100001"},
        timeout=20.0,
    )


def _rows(payload):
    """UW responses are usually {'data': [...]} or a bare list."""
    if isinstance(payload, dict):
        d = payload.get("data", payload)
        return d if isinstance(d, list) else [d]
    if isinstance(payload, list):
        return payload
    return []


def _num(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                pass
    return default


def _str(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def _iv(c, t):
    try:
        r = c.get(f"/api/stock/{t}/interpolated-iv")
        rows = _rows(r.json())
        if not rows:
            return {}
        last = rows[-1] if isinstance(rows[-1], dict) else rows[0]
        iv = _num(last, "implied_volatility", "atm_iv", "iv")
        return {
            "iv_pct": round(iv * 100, 1) if iv is not None and iv < 5 else (round(iv, 1) if iv else None),
            "iv_rank": _num(last, "iv_rank", "iv_percentile", "rank"),
        }
    except Exception:
        return {}


def _gex(c, t):
    try:
        r = c.get(f"/api/stock/{t}/spot-exposures/strike")
        rows = _rows(r.json())
        net = 0.0
        seen = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            cg = _num(row, "call_gamma_oi", "call_gamma", "call_gex")
            pg = _num(row, "put_gamma_oi", "put_gamma", "put_gex")
            g = _num(row, "gamma_per_one_percent_move_oi", "gex", "gamma")
            if cg is not None or pg is not None:
                net += (cg or 0) - (pg or 0); seen = True
            elif g is not None:
                net += g; seen = True
        if not seen:
            return {}
        return {"net_gex": round(net),
                "gex_sign": "positive (moves dampen)" if net >= 0 else "negative (moves amplify)"}
    except Exception:
        return {}


def _pc(c, t):
    try:
        r = c.get(f"/api/stock/{t}/options-volume")
        rows = _rows(r.json())
        if not rows:
            return {}
        last = rows[-1] if isinstance(rows[-1], dict) else rows[0]
        pcr = _num(last, "put_call_ratio", "pc_ratio")
        cv = _num(last, "call_volume", "calls")
        pv = _num(last, "put_volume", "puts")
        if pcr is None and cv and pv:
            pcr = round(pv / cv, 2) if cv else None
        return {"pc_ratio": round(pcr, 2) if pcr is not None else None,
                "call_vol": int(cv) if cv else None, "put_vol": int(pv) if pv else None}
    except Exception:
        return {}


def _flow(c, t):
    try:
        r = c.get("/api/option-trades/flow-alerts",
                  params={"ticker_symbol": t, "min_premium": 50000, "limit": 8})
        rows = _rows(r.json())
        call_prem = put_prem = 0.0
        top = []
        for row in rows[:8]:
            if not isinstance(row, dict):
                continue
            typ = (_str(row, "type", "option_type", "side") or "").lower()
            prem = _num(row, "total_premium", "premium", default=0) or 0
            if "call" in typ:
                call_prem += prem
            elif "put" in typ:
                put_prem += prem
            top.append({
                "type": "C" if "call" in typ else ("P" if "put" in typ else "?"),
                "strike": _num(row, "strike"),
                "premium": round(prem),
                "vol": _num(row, "volume", "total_size", "size"),
                "oi": _num(row, "open_interest", "oi"),
            })
        if not top:
            return {}
        return {"call_prem": round(call_prem), "put_prem": round(put_prem),
                "flow_tilt": "call-heavy" if call_prem >= put_prem else "put-heavy",
                "top_flow": top[:5]}
    except Exception:
        return {}


def _darkpool(c, t):
    try:
        r = c.get(f"/api/darkpool/{t}", params={"limit": 20})
        rows = _rows(r.json())
        prints = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            prints.append({"price": _num(row, "price"),
                           "size": _num(row, "size", "volume"),
                           "at": _str(row, "executed_at", "timestamp", "date")})
        prints = [p for p in prints if p["size"]]
        prints.sort(key=lambda p: p["size"] or 0, reverse=True)
        if not prints:
            return {}
        return {"darkpool_top": prints[:3]}
    except Exception:
        return {}


def _parse_occ(sym: str):
    """Parse an OCC option symbol (e.g. AAPL260117C00150000) -> (expiry, type, strike)."""
    import re
    m = re.search(r"(\d{6})([CP])(\d{8})$", (sym or "").replace(" ", ""))
    if not m:
        return None
    yy, mm, dd = m.group(1)[:2], m.group(1)[2:4], m.group(1)[4:6]
    typ = "call" if m.group(2) == "C" else "put"
    return f"20{yy}-{mm}-{dd}", typ, int(m.group(3)) / 1000.0


def _compute_maxpain(contracts: list[tuple]):
    """
    contracts: list of (expiry, 'call'/'put', strike, open_interest).
    Max pain = strike that minimises total payout to option holders at expiry,
    for the nearest expiry. Pure function (unit-testable, no network).
    """
    from collections import defaultdict
    import datetime
    exps = defaultdict(lambda: defaultdict(lambda: {"c": 0.0, "p": 0.0}))
    for exp, typ, strike, oi in contracts:
        if strike is None or not exp:
            continue
        node = exps[exp][strike]
        node["c" if typ == "call" else "p"] += (oi or 0.0)
    if not exps:
        return {}
    today = datetime.date.today().isoformat()
    future = [e for e in exps if e >= today] or list(exps)
    exp = min(future)
    strikes = sorted(exps[exp])
    if len(strikes) < 3:
        return {}
    best, best_pay = None, None
    for K in strikes:
        pay = 0.0
        for s in strikes:
            node = exps[exp][s]
            if s < K:
                pay += (K - s) * node["c"]      # ITM calls cost writers
            elif s > K:
                pay += (s - K) * node["p"]       # ITM puts cost writers
        if best_pay is None or pay < best_pay:
            best_pay, best = pay, K
    return {"max_pain": round(best, 2), "mp_expiry": exp}


def _maxpain(c, t):
    try:
        r = c.get(f"/api/stock/{t}/option-contracts")
        rows = _rows(r.json())
        contracts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            oi = _num(row, "open_interest", "oi", default=0) or 0
            strike = _num(row, "strike")
            typ = (_str(row, "option_type", "type") or "").lower()
            exp = _str(row, "expiry", "expiration", "expires_at", "expiration_date")
            if strike is None or not typ or not exp:
                parsed = _parse_occ(_str(row, "option_symbol", "symbol", "id") or "")
                if parsed:
                    exp, typ, strike = parsed
            typ = "call" if "c" in typ[:1] else ("put" if "p" in typ[:1] else "")
            if strike is not None and typ and exp:
                contracts.append((exp, typ, strike, oi))
        return _compute_maxpain(contracts)
    except Exception:
        return {}


def enrich(flagged: list[dict], watch_light: list[dict] | None = None) -> list[dict]:
    """Attach an `options` dict to each flagged result, and a lighter `options_light`
    dict to each watch_light result (in place). Returns flagged."""
    c = _client()
    if c is None:
        return flagged
    watch_light = watch_light or []
    with c:
        for r in flagged:
            t = r["ticker"]
            opt = {}
            for fn in (_iv, _gex, _pc, _flow, _darkpool, _maxpain):
                opt.update(fn(c, t))
                time.sleep(0.15)            # be gentle with rate limits
            r["options"] = opt or None
        for r in watch_light:               # light pass: IV + P/C + flow tilt only
            t = r["ticker"]
            opt = {}
            for fn in (_iv, _pc, _flow):
                opt.update(fn(c, t))
                time.sleep(0.15)
            r["options_light"] = opt or None
    return flagged


def options_evidence(opt: dict | None) -> list[str]:
    """Render option metrics as evidence bullet strings (used by fallback narrative)."""
    if not opt:
        return []
    out = []
    if opt.get("iv_pct") is not None:
        rank = f", IV rank {opt['iv_rank']:.0f}" if opt.get("iv_rank") is not None else ""
        out.append(f"• <b>ATM IV ~{opt['iv_pct']}%</b>{rank} (UW interpolated-iv).")
    if opt.get("net_gex") is not None:
        out.append(f"• <b>Net GEX {opt['net_gex']:,}</b> — {opt['gex_sign']} (UW spot-exposures).")
    if opt.get("max_pain") is not None:
        out.append(f"• <b>Max pain ${opt['max_pain']}</b> (nearest expiry {opt.get('mp_expiry','')}) "
                   f"— the magnet level option dealers gravitate toward (UW option-contracts OI).")
    if opt.get("pc_ratio") is not None:
        out.append(f"• <b>Put/Call {opt['pc_ratio']}</b> "
                   f"(calls {opt.get('call_vol')}, puts {opt.get('put_vol')}).")
    if opt.get("top_flow"):
        bits = ", ".join(
            f"{f['type']}{('$'+str(int(f['strike']))) if f.get('strike') else ''} "
            f"(${f['premium']:,})" for f in opt["top_flow"][:3])
        out.append(f"• <b>Flow {opt.get('flow_tilt','')}</b> — calls ${opt.get('call_prem',0):,} vs "
                   f"puts ${opt.get('put_prem',0):,}; top: {bits}.")
    if opt.get("darkpool_top"):
        dp = opt["darkpool_top"][0]
        out.append(f"• <b>Dark pool</b> — largest print {int(dp['size']):,} @ "
                   f"${dp['price']} (UW darkpool).")
    return out


if __name__ == "__main__":
    import json, sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(json.dumps(enrich([{"ticker": tk}]), indent=2, default=str))
