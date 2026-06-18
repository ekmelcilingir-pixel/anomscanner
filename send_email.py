"""
send_email.py — optional daily e-mail notification for Anomscanner.

After main.py renders the report it calls maybe_send(...). If the Gmail
secrets are present, a clean summary e-mail goes out (Claude's one-line
summary + a compact table of flagged names + a direct link to the report).
If the secrets are missing it no-ops, so the pipeline never breaks.

Secrets / env vars used:
  GMAIL_USER          the sending Gmail address           (required to send)
  GMAIL_APP_PASSWORD  a Gmail *app password*, not the login password (required)
  MAIL_TO             recipient(s), comma-separated; defaults to GMAIL_USER
  SITE_BASE_URL       Pages base, no trailing slash
                      (default https://ekmelcilingir-pixel.github.io/anomscanner)

Test locally:  python send_email.py   (sends a tiny sample if secrets are set)
"""

from __future__ import annotations
import os, smtplib, ssl, html, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

DEFAULT_BASE = "https://ekmelcilingir-pixel.github.io/anomscanner"
DOT = {"r": "\U0001F534", "a": "\U0001F7E1", "g": "\U0001F7E2"}  # 🔴 🟡 🟢


def _creds():
    user = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to = os.environ.get("MAIL_TO", "").strip() or user
    return user, pw, to


def _base_url():
    return os.environ.get("SITE_BASE_URL", DEFAULT_BASE).rstrip("/")


def _color_for(card, row):
    """Pill colour: prefer Claude's card colour, else derive from the move."""
    if card and card.get("color") in DOT:
        return card["color"]
    ret = float(row.get("today_ret_pct", 0) or 0)
    below = row.get("close") and row.get("z_abs", 0) and ret < 0
    if ret <= -3 or (below and ret < 0):
        return "r"
    if ret < 0:
        return "a"
    return "g"


def build_html(scan: dict, narr: dict, day: datetime.date, report_url: str,
               platform_url: str) -> str:
    flagged = sorted(scan.get("flagged", []),
                     key=lambda r: r.get("impact", 0), reverse=True)
    cards = {c.get("ticker", "").upper(): c for c in narr.get("cards", [])}
    summary = html.escape(narr.get("summary", "") or "")
    tone = html.escape(narr.get("tone", "") or "")
    n_scanned = scan.get("n_scanned", "")
    n_hold = scan.get("n_holdings", "")
    thr = scan.get("threshold", 40)
    below = scan.get("below", [])

    rows = ""
    for r in flagged:
        t = r.get("ticker", "").upper()
        c = cards.get(t)
        color = _color_for(c, r)
        pill = html.escape((c or {}).get("pill_label", "WATCH"))
        ret = r.get("today_ret_pct", 0)
        ret_col = "#ff5d6c" if (ret or 0) < 0 else "#27c281"
        rows += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #21262d;font-weight:700;color:#e6edf3">{t}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #21262d;color:{ret_col};font-weight:600;text-align:right">{ret:+.2f}%</td>
          <td style="padding:10px 12px;border-bottom:1px solid #21262d;color:#e6edf3;text-align:right">{r.get('anomaly','')}/100</td>
          <td style="padding:10px 12px;border-bottom:1px solid #21262d;color:#e6edf3;text-align:right;font-weight:700">{r.get('impact','')}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #21262d;color:#8b949e">{DOT.get(color,'')} {pill}</td>
        </tr>"""

    if not flagged:
        rows = """<tr><td colspan="5" style="padding:16px;color:#8b949e;text-align:center">
        No names crossed the anomaly threshold today. Portfolio quiet.</td></tr>"""

    below_line = ""
    if below:
        below_line = (f'<p style="margin:14px 0 0;color:#8b949e;font-size:13px">'
                      f'{len(below)} more name(s) scanned below the {thr} threshold '
                      f'(see the full report for the watchlist note).</p>')

    btn = ("display:inline-block;padding:11px 20px;border-radius:8px;"
           "text-decoration:none;font-weight:600;font-size:14px")

    return f"""<!doctype html><html><body style="margin:0;background:#0d1117;
    font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
    <div style="max-width:640px;margin:0 auto;padding:24px">

      <div style="display:flex;align-items:baseline;justify-content:space-between;
        border-bottom:1px solid #30363d;padding-bottom:14px">
        <span style="font-size:20px;font-weight:800;color:#e6edf3;letter-spacing:.3px">
          Anomscanner</span>
        <span style="color:#8b949e;font-size:14px">{day.isoformat()}</span>
      </div>

      <p style="color:#e6edf3;font-size:15px;line-height:1.55;margin:18px 0 4px">{summary}</p>
      {f'<p style="color:#5ba7ff;font-size:12px;margin:0 0 6px;text-transform:uppercase;letter-spacing:.5px">{tone}</p>' if tone else ''}
      <p style="color:#8b949e;font-size:13px;margin:6px 0 18px">
        {len(flagged)} flagged of {n_scanned} scanned · {n_hold} holdings · threshold {thr}</p>

      <table style="width:100%;border-collapse:collapse;background:#161b22;
        border:1px solid #30363d;border-radius:10px;overflow:hidden;font-size:14px">
        <thead>
          <tr style="background:#1c2128">
            <th style="padding:10px 12px;text-align:left;color:#8b949e;font-weight:600;font-size:12px">TICKER</th>
            <th style="padding:10px 12px;text-align:right;color:#8b949e;font-weight:600;font-size:12px">TODAY</th>
            <th style="padding:10px 12px;text-align:right;color:#8b949e;font-weight:600;font-size:12px">ANOMALY</th>
            <th style="padding:10px 12px;text-align:right;color:#8b949e;font-weight:600;font-size:12px">IMPACT</th>
            <th style="padding:10px 12px;text-align:left;color:#8b949e;font-weight:600;font-size:12px">ACTION</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      {below_line}

      <div style="margin:24px 0 8px">
        <a href="{report_url}" style="{btn};background:#27c281;color:#06281c">Open full report →</a>
        <a href="{platform_url}" style="{btn};background:#1c2128;color:#e6edf3;border:1px solid #30363d;margin-left:8px">All reports</a>
      </div>

      <p style="color:#6e7681;font-size:12px;line-height:1.5;margin-top:22px;
        border-top:1px solid #21262d;padding-top:14px">
        Automated daily scan of your portfolio (price / volume / trend anomalies,
        portfolio-impact weighted). Educational signal only — not investment advice.
        Generated by your own Anomscanner pipeline.</p>

    </div></body></html>"""


def maybe_send(scan: dict, narr: dict, day: datetime.date, filename: str) -> bool:
    """Send the notification if Gmail secrets exist. Returns True if sent."""
    user, pw, to = _creds()
    if not (user and pw):
        print("  e-mail: GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping notification.")
        return False

    base = _base_url()
    report_url = f"{base}/reports/{filename}"
    platform_url = f"{base}/"
    flagged = scan.get("flagged", [])
    tickers = ", ".join(r["ticker"] for r in sorted(
        flagged, key=lambda r: r.get("impact", 0), reverse=True)[:4])
    subject = (f"Anomscanner · {day.isoformat()} · "
               f"{len(flagged)} anomal{'y' if len(flagged)==1 else 'ies'}"
               + (f" ({tickers})" if tickers else " — portfolio quiet"))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    plain = (f"{narr.get('summary','')}\n\n"
             f"{len(flagged)} flagged of {scan.get('n_scanned','?')} scanned.\n"
             f"Full report: {report_url}\nAll reports: {platform_url}\n")
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(build_html(scan, narr, day, report_url, platform_url),
                        "html", "utf-8"))

    recipients = [a.strip() for a in to.split(",") if a.strip()]
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(user, pw)
            s.sendmail(user, recipients, msg.as_string())
        print(f"  e-mail: sent to {', '.join(recipients)} ✓")
        return True
    except Exception as e:
        # never fail the pipeline because of e-mail
        print(f"  e-mail: send failed ({e.__class__.__name__}: {e}) — continuing.")
        return False


if __name__ == "__main__":
    # tiny smoke test
    demo_scan = {"flagged": [
        {"ticker": "ALAB", "today_ret_pct": -5.1, "anomaly": 62, "impact": 12,
         "z_abs": -2.3, "close": 90},
        {"ticker": "BE", "today_ret_pct": 4.2, "anomaly": 48, "impact": 5,
         "z_abs": 2.0, "close": 30}],
        "below": [{"ticker": "MU"}], "n_scanned": 27, "n_holdings": 26, "threshold": 40}
    demo_narr = {"summary": "Two holdings moved sharply; ALAB is the main portfolio risk today.",
                 "tone": "risk-weighted",
                 "cards": [{"ticker": "ALAB", "pill_label": "WATCH · NO ADD", "color": "r"},
                           {"ticker": "BE", "pill_label": "MONITOR", "color": "a"}]}
    maybe_send(demo_scan, demo_narr, datetime.date.today(), "anomscanner-demo.html")
