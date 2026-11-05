"""
Co-relation Matrix for the current holdings -- shared by the Streamlit dashboards
(sharpe_dashboard.py, sharpe_dashboard_dhan.py) and the Flask webapp, so the three
can never disagree.

The matrix is one square grid, tickers in alphabetical order on both axes:

  * lower-left  (incl. diagonal): FULL-YEAR correlation of daily log returns
                (last 252 trading days), coloured on a red-blue scale, -1..+1.
  * upper-right (bold): LAST-60-DAY correlation, printed as a number but COLOURED by how
                much it changed versus the full year (60d - full), on a purple-orange
                scale, -0.4..+0.4 (orange = more correlated lately, purple = less).

Usage
-----
    import corr_matrix as cm
    html, message = cm.render(prices_df, holdings)   # holdings: iterable of tickers
    # `message` is a plain-text reason when there is nothing to draw (no open positions,
    # fewer than two priced holdings); otherwise `html` is a self-contained <div> that
    # needs the stylesheet in cm.CSS on the page.

prices_df: index = ticker, columns = trading dates (what momentum_lib.load_prices returns).
"""
from __future__ import annotations

import html as _html

import numpy as np
import pandas as pd

FULL_DAYS = 252      # "full year" window, in daily returns
RECENT_DAYS = 60     # recent window, in daily returns
MIN_OBS = 20         # a pair needs at least this many overlapping returns to get a number
CHANGE_CLIP = 0.4    # upper-right colour scale spans +/- this change in correlation

# matplotlib's RdBu and PuOr_r (11-class ColorBrewer), low -> high, so the dashboards
# match the approved heatmap without depending on matplotlib.
_RDBU = ["#67001f", "#b2182b", "#d6604d", "#f4a582", "#fddbc7", "#f7f7f7",
         "#d1e5f0", "#92c5de", "#4393c3", "#2166ac", "#053061"]
_PUOR_R = ["#2d004b", "#542788", "#8073ac", "#b2abd2", "#d8daeb", "#f7f7f7",
           "#fee0b6", "#fdb863", "#e08214", "#b35806", "#7f3b08"]


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _lerp_color(stops: list[str], t: float) -> tuple[int, int, int]:
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    a, b = _hex_to_rgb(stops[i]), _hex_to_rgb(stops[i + 1])
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


def _css_rgb(rgb: tuple[int, int, int]) -> str:
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def corr_color(v: float) -> str:
    """Correlation -1..+1 -> RdBu colour (blue = positive, red = negative)."""
    return _css_rgb(_lerp_color(_RDBU, (v + 1.0) / 2.0))


def change_color(d: float) -> str:
    """Change in correlation -0.4..+0.4 -> PuOr_r colour (orange = up, purple = down)."""
    return _css_rgb(_lerp_color(_PUOR_R, (d + CHANGE_CLIP) / (2 * CHANGE_CLIP)))


# ── computation ───────────────────────────────────────────────────────────────

def compute(prices_df: pd.DataFrame, holdings) -> dict | None:
    """Full-year and 60-day return correlations for `holdings` (alphabetical).
    None if fewer than two holdings have price data."""
    wanted = sorted({str(t).strip() for t in holdings if str(t).strip()})
    present = [t for t in wanted if t in prices_df.index]
    missing = [t for t in wanted if t not in prices_df.index]
    if len(present) < 2:
        return {"tickers": present, "missing": missing, "too_few": True}

    px = prices_df.loc[present].T.astype(float)
    px = px.sort_index().dropna(how="all")            # drop market holidays / empty columns
    ret = np.log(px).diff().iloc[1:].replace([np.inf, -np.inf], np.nan)
    full = ret.tail(FULL_DAYS)
    recent = ret.tail(RECENT_DAYS)

    F = full.corr(min_periods=MIN_OBS).reindex(index=present, columns=present).to_numpy(dtype=float, copy=True)
    R = recent.corr(min_periods=MIN_OBS).reindex(index=present, columns=present).to_numpy(dtype=float, copy=True)
    n = len(present)
    for i, t in enumerate(present):                    # the diagonal is 1 whenever there is any data
        ok = full[t].count() >= MIN_OBS
        F[i, i] = 1.0 if ok else np.nan
        R[i, i] = 1.0 if recent[t].count() >= MIN_OBS else np.nan
    D = R - F

    n_full = {t: int(full[t].count()) for t in present}
    n_rec = {t: int(recent[t].count()) for t in present}
    # "short" = fewer returns than the window offers (recently listed, suspended, or gappy data)
    short = [t for t in present if n_full[t] < len(full) or n_rec[t] < len(recent)]

    iu = np.triu_indices(n, 1)

    def _avg(M):
        v = M[iu]
        v = v[~np.isnan(v)]
        return float(v.mean()) if len(v) else None

    last = px.index[-1]
    return {
        "tickers": present, "missing": missing, "too_few": False,
        "F": F, "R": R, "D": D,
        "n_full": n_full, "n_rec": n_rec, "short": short,
        "full_days": len(full), "recent_days": len(recent),
        "last_date": last,
        "avg_full": _avg(F), "avg_rec": _avg(R),
    }


# ── rendering ─────────────────────────────────────────────────────────────────

CSS = """
.cm-wrap{font-family:var(--font-ui,'Geist',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif);color:#0e1726;}
.cm-summary{display:flex;flex-wrap:wrap;gap:8px 22px;align-items:baseline;margin:2px 0 6px;font-size:14px;color:#3c4657;}
.cm-summary b{color:#0e1726;font-weight:700;}
.cm-sub{font-size:13px;color:#5e6878;margin:0 0 14px;line-height:1.5;}
.cm-scroll{overflow-x:auto;padding-bottom:4px;}
table.cm-grid{border-collapse:separate;border-spacing:2px;table-layout:fixed;}
table.cm-grid th,table.cm-grid td{padding:0;}
.cm-colh{height:112px;width:52px;vertical-align:bottom;text-align:left;}
.cm-colh div{writing-mode:vertical-rl;transform:rotate(180deg);white-space:nowrap;height:108px;margin:0 auto;font-size:12px;font-weight:600;color:#3c4657;}
.cm-rowh{text-align:right;padding-right:10px !important;white-space:nowrap;font-size:12px;font-weight:600;color:#3c4657;width:104px;}
.cm-c{width:52px;height:36px;text-align:center;vertical-align:middle;font-size:12px;font-family:var(--font-mono,'Geist Mono',ui-monospace,Menlo,monospace);border-radius:3px;}
.cm-up{font-weight:700;}
.cm-na{background:#eceef2;color:#98a1ae;font-weight:400;}
.cm-short{color:#98a1ae !important;font-style:italic;}
.cm-legends{display:flex;flex-wrap:wrap;gap:18px 48px;margin:16px 0 4px;}
.cm-lg{width:300px;max-width:100%;}
.cm-lg-t{font-size:12.5px;font-weight:600;color:#3c4657;margin-bottom:5px;}
.cm-bar{height:12px;border-radius:6px;}
.cm-ticks{display:flex;justify-content:space-between;font-size:11.5px;color:#5e6878;margin-top:3px;font-family:var(--font-mono,'Geist Mono',ui-monospace,Menlo,monospace);}
.cm-lg-n{font-size:12px;color:#5e6878;margin-top:3px;}
.cm-notes{margin-top:12px;font-size:12.5px;color:#5e6878;line-height:1.55;}
"""


def _gradient(stops: list[str]) -> str:
    pct = [f"{s} {i * 100 / (len(stops) - 1):.0f}%" for i, s in enumerate(stops)]
    return "linear-gradient(to right," + ",".join(pct) + ")"


def _fmt(v: float) -> str:
    return f"{v:.2f}".replace("-0.00", "0.00")  # no "-0.00"


def _fmt_date(d) -> str:
    d = pd.Timestamp(d)
    return f"{d.day} {d.strftime('%b %Y')}"


def build_html(m: dict) -> str:
    """The matrix card as one compact <div> (no blank lines / indentation, so it is safe to
    pass through st.markdown(..., unsafe_allow_html=True) as well as a Jinja |safe)."""
    tk, F, R, D = m["tickers"], m["F"], m["R"], m["D"]
    n = len(tk)
    short = set(m["short"])
    esc = _html.escape
    p = []

    avg_f = "n/a" if m["avg_full"] is None else _fmt(m["avg_full"])
    avg_r = "n/a" if m["avg_rec"] is None else _fmt(m["avg_rec"])
    p.append('<div class="cm-wrap">')
    p.append(f'<div class="cm-summary"><span><b>{n}</b> holdings</span>'
             f'<span>Avg pairwise correlation &mdash; full year: <b>{avg_f}</b></span>'
             f'<span>last {m["recent_days"]} days: <b>{avg_r}</b></span></div>')
    p.append(f'<p class="cm-sub">Lower-left: full year ({m["full_days"]} trading days to {_fmt_date(m["last_date"])}), '
             f'colour = correlation. Upper-right (bold): last {m["recent_days"]} days, '
             f'colour = change vs the full year. The two colour bars below are the legend.</p>')

    p.append('<div class="cm-scroll"><table class="cm-grid"><thead><tr><th></th>')
    for t in tk:
        cls = ' class="cm-short"' if t in short else ""
        p.append(f'<th class="cm-colh"><div{cls}>{esc(t)}{"&dagger;" if t in short else ""}</div></th>')
    p.append("</tr></thead><tbody>")

    for i, ti in enumerate(tk):
        cls = " cm-short" if ti in short else ""
        p.append(f'<tr><th class="cm-rowh{cls}">{esc(ti)}{"&dagger;" if ti in short else ""}</th>')
        for j, tj in enumerate(tk):
            if i == j:
                if np.isnan(F[i, i]):
                    p.append('<td class="cm-c cm-na">n/a</td>')
                else:
                    p.append(f'<td class="cm-c" style="background:{corr_color(1.0)};color:#fff">1.00</td>')
                continue
            f, r, d = F[i, j], R[i, j], D[i, j]
            tip = (f"{esc(ti)} / {esc(tj)}&#10;Full year: {'n/a' if np.isnan(f) else _fmt(f)}"
                   f"&#10;Last {m['recent_days']}d: {'n/a' if np.isnan(r) else _fmt(r)}"
                   f"&#10;Change: {'n/a' if np.isnan(d) else f'{d:+.2f}'}")
            if i > j:                                   # lower-left: full-year correlation
                if np.isnan(f):
                    p.append(f'<td class="cm-c cm-na" title="{tip}">n/a</td>')
                else:
                    col = "#fff" if abs(f) > 0.5 else "#0e1726"
                    p.append(f'<td class="cm-c" title="{tip}" style="background:{corr_color(f)};color:{col}">{_fmt(f)}</td>')
            else:                                       # upper-right: 60d value, coloured by the change
                if np.isnan(r):
                    p.append(f'<td class="cm-c cm-na" title="{tip}">n/a</td>')
                elif np.isnan(d):
                    p.append(f'<td class="cm-c cm-up" title="{tip}" style="background:#eceef2;color:#0e1726">{_fmt(r)}</td>')
                else:
                    col = "#fff" if abs(d) > 0.25 else "#0e1726"
                    p.append(f'<td class="cm-c cm-up" title="{tip}" style="background:{change_color(d)};color:{col}">{_fmt(r)}</td>')
        p.append("</tr>")
    p.append("</tbody></table></div>")

    p.append('<div class="cm-legends">')
    p.append(f'<div class="cm-lg"><div class="cm-lg-t">Full-year correlation (lower-left)</div>'
             f'<div class="cm-bar" style="background:{_gradient(_RDBU)}"></div>'
             f'<div class="cm-ticks"><span>-1.0</span><span>-0.5</span><span>0</span><span>0.5</span><span>1.0</span></div></div>')
    p.append(f'<div class="cm-lg"><div class="cm-lg-t">{m["recent_days"]}d colour = change vs full year (upper-right)</div>'
             f'<div class="cm-bar" style="background:{_gradient(_PUOR_R)}"></div>'
             f'<div class="cm-ticks"><span>-0.4</span><span>-0.2</span><span>0</span><span>+0.2</span><span>+0.4</span></div>'
             f'<div class="cm-lg-n">orange = more correlated lately, purple = less</div></div>')
    p.append("</div>")

    notes = []
    if short:
        parts = ", ".join(f"{esc(t)} ({m['n_full'][t]} of {m['full_days']} days, "
                          f"{m['n_rec'][t]} of {m['recent_days']} in the recent window)" for t in tk if t in short)
        notes.append(f"&dagger; Short or incomplete price history: {parts}. Pairs with fewer than "
                     f"{MIN_OBS} overlapping days are shown as n/a; the rest use the days available.")
    if m["missing"]:
        notes.append("No price data for: " + ", ".join(esc(t) for t in m["missing"]) + " (left out of the matrix).")
    if notes:
        p.append('<div class="cm-notes">' + "<br>".join(notes) + "</div>")
    p.append("</div>")
    return "".join(p)


def render(prices_df: pd.DataFrame, holdings) -> tuple[str | None, str | None]:
    """(html, None) when there is a matrix to draw, else (None, plain-text message)."""
    held = [t for t in holdings if str(t).strip()]
    if not held:
        return None, "No open positions — there is nothing to correlate yet."
    m = compute(prices_df, held)
    if m.get("too_few"):
        msg = "Need at least two holdings with price data to build a correlation matrix."
        if m["missing"]:
            msg += " No price data for: " + ", ".join(m["missing"]) + "."
        return None, msg
    return build_html(m), None
