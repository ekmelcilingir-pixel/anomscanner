"""
scan.py — Anomtara anomaly scanner (price/volume/trend layer)

Pulls ~14 months of daily OHLCV from Yahoo Finance for every holding + watchlist
ticker (plus its sector ETF) and computes, per name:

  z_abs     absolute return z-score vs the stock's own last ~60 trading days
  z_rel     return relative to its sector ETF, z-scored the same way
  vol_ratio today's volume / 20-day average volume
  sma50/200 + flags: below_sma50, death_cross (sma50 < sma200)
  gap_atr   today's open gap measured in ATR(14) units
  hi52/lo52 52-week range position

It then produces:

  anomaly   0-100 composite "how unusual" score
  impact    Portfolio Impact Score = round(anomaly * weight_pct * direction / 10)
            direction = 1.5 if the move threatens a long position, else 1.0

Names with anomaly >= THRESHOLD are flagged for the report; the rest go to the
"below-threshold watch" list. Options data (gamma/IV/flow) is NOT handled here —
see fetch_options.py (optional, needs Unusual Whales).
"""

from __future__ import annotations
import math
import pandas as pd
import numpy as np
import yfinance as yf

THRESHOLD = 40          # anomaly score that forces a manual look
LOOKBACK_Z = 60         # trading days for z-score normal
VOL_WINDOW = 20         # trading days for average volume
ATR_WINDOW = 14


def _safe(x, default=0.0):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return default
        return float(x)
    except Exception:
        return default


def _atr(df: pd.DataFrame, n: int = ATR_WINDOW) -> float:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return _safe(tr.rolling(n).mean().iloc[-1])


def _download(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Download daily OHLCV for a list of tickers, return {ticker: df}."""
    uniq = sorted(set(t.upper() for t in tickers if t))
    raw = yf.download(uniq, period="14mo", interval="1d",
                      auto_adjust=False, group_by="ticker",
                      threads=True, progress=False)
    out: dict[str, pd.DataFrame] = {}
    for t in uniq:
        try:
            df = raw[t] if len(uniq) > 1 else raw
            df = df.dropna(subset=["Close"])
            if len(df) > 30:
                out[t] = df
        except Exception:
            continue
    return out


def _returns(df: pd.DataFrame) -> pd.Series:
    return df["Close"].pct_change()


def scan(holdings: list[dict], watchlist: list[dict] | None = None) -> dict:
    """
    holdings:  [{ticker, shares, cost, sector_etf}]
    watchlist: [{ticker, sector_etf}]  (optional)
    Returns a dict ready for narrate.py / render.py.
    """
    watchlist = watchlist or []
    all_rows = holdings + watchlist
    tickers = [r["ticker"] for r in all_rows]
    etfs = [r.get("sector_etf", "SPY") for r in all_rows]
    data = _download(tickers + etfs + ["SPY"])

    # current portfolio value for weights
    port_value = 0.0
    for h in holdings:
        df = data.get(h["ticker"].upper())
        if df is None:
            continue
        px = _safe(df["Close"].iloc[-1])
        port_value += px * _safe(h.get("shares"), 0)
    port_value = port_value or 1.0

    results = []
    for row in all_rows:
        t = row["ticker"].upper()
        df = data.get(t)
        if df is None or len(df) < LOOKBACK_Z + 2:
            continue
        etf = row.get("sector_etf", "SPY").upper()
        edf = data.get(etf)

        r = _returns(df)
        today_ret = _safe(r.iloc[-1])
        win = r.iloc[-(LOOKBACK_Z + 1):-1]
        mu, sd = _safe(win.mean()), _safe(win.std(), 1e-9) or 1e-9
        z_abs = (today_ret - mu) / sd

        z_rel = 0.0
        etf_ret = None
        if edf is not None and len(edf) >= LOOKBACK_Z + 2:
            er = _returns(edf)
            etf_ret = _safe(er.iloc[-1])
            rel = (r - er).dropna()
            rwin = rel.iloc[-(LOOKBACK_Z + 1):-1]
            rmu, rsd = _safe(rwin.mean()), _safe(rwin.std(), 1e-9) or 1e-9
            z_rel = (_safe(rel.iloc[-1]) - rmu) / rsd

        vol = df["Volume"]
        avg_vol = _safe(vol.iloc[-(VOL_WINDOW + 1):-1].mean(), 1.0) or 1.0
        vol_ratio = _safe(vol.iloc[-1]) / avg_vol

        close = _safe(df["Close"].iloc[-1])
        sma50 = _safe(df["Close"].rolling(50).mean().iloc[-1])
        sma200 = _safe(df["Close"].rolling(200).mean().iloc[-1])
        below_sma50 = close < sma50 if sma50 else False
        death_cross = (sma50 < sma200) if (sma50 and sma200) else False

        atr = _atr(df) or 1e-9
        gap = _safe(df["Open"].iloc[-1]) - _safe(df["Close"].iloc[-2])
        gap_atr = abs(gap) / atr

        hi52 = _safe(df["High"].iloc[-252:].max())
        lo52 = _safe(df["Low"].iloc[-252:].min())

        # ---- composite anomaly score (0-100), transparent blend ----
        c_price = min(abs(z_abs) / 3.0, 1.0) * 30
        c_rel = min(abs(z_rel) / 3.0, 1.0) * 25
        c_vol = min(max(vol_ratio - 1.0, 0.0) / 2.0, 1.0) * 20
        c_trend = (10 if below_sma50 else 0) + (10 if death_cross else 0)
        c_gap = min(gap_atr / 2.0, 1.0) * 5
        anomaly = round(min(c_price + c_rel + c_vol + c_trend + c_gap, 100))

        # ---- position / impact ----
        h = next((x for x in holdings if x["ticker"].upper() == t), None)
        shares = _safe(h.get("shares"), 0) if h else 0
        cost = _safe(h.get("cost"), 0) if h else 0
        pos_value = close * shares
        weight_pct = round(100 * pos_value / port_value, 1) if h else 0.0
        # threat: long position + bearish move (down day / below 50d)
        bearish = (today_ret < 0) or below_sma50
        direction = 1.5 if (h and bearish) else 1.0
        impact = round(anomaly * weight_pct * direction / 10) if h else 0

        results.append({
            "ticker": t,
            "is_holding": bool(h),
            "sector_etf": etf,
            "close": round(close, 2),
            "today_ret_pct": round(today_ret * 100, 2),
            "etf_ret_pct": round(etf_ret * 100, 2) if etf_ret is not None else None,
            "z_abs": round(z_abs, 2),
            "z_rel": round(z_rel, 2),
            "vol_ratio": round(vol_ratio, 2),
            "sma50": round(sma50, 2),
            "sma200": round(sma200, 2),
            "below_sma50": below_sma50,
            "death_cross": death_cross,
            "gap_atr": round(gap_atr, 2),
            "hi52": round(hi52, 2),
            "lo52": round(lo52, 2),
            "shares": shares,
            "cost": cost,
            "pos_value": round(pos_value),
            "weight_pct": weight_pct,
            "direction": direction,
            "anomaly": anomaly,
            "impact": impact,
        })

    flagged = sorted([r for r in results if r["anomaly"] >= THRESHOLD],
                     key=lambda x: x["impact"], reverse=True)
    below = sorted([r for r in results if r["anomaly"] < THRESHOLD],
                   key=lambda x: x["anomaly"], reverse=True)

    return {
        "n_holdings": len(holdings),
        "n_scanned": len(results),
        "threshold": THRESHOLD,
        "flagged": flagged,
        "below": below,
    }


if __name__ == "__main__":
    import json, csv, sys
    def load(path):
        try:
            with open(path) as f:
                return list(csv.DictReader(f))
        except FileNotFoundError:
            return []
    h = [{"ticker": r["ticker"], "shares": r.get("shares"),
          "cost": r.get("cost"), "sector_etf": r.get("sector_etf", "SPY")}
         for r in load("holdings.csv")]
    w = [{"ticker": r["ticker"], "sector_etf": r.get("sector_etf", "SPY")}
         for r in load("watchlist.csv")]
    print(json.dumps(scan(h, w), indent=2))
