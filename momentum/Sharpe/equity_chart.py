"""
equity_chart.py
================
Shared equity-curve chart component for the Streamlit dashboards
(sharpe_dashboard.py and sharpe_dashboard_dhan.py). Renders a custom
HTML/SVG line chart — hover crosshair, per-series tooltip, direct end
labels — in place of the previous Altair chart, styled to match this
app's existing palette (navy accent, slate-gray ink, light card surfaces).

Import and call render_equity_curve(eq_df) from either dashboard file.
"""
import json

import pandas as pd
import streamlit.components.v1 as components

# Matches the dashboard's existing inline CSS (see st.markdown block near
# the top of sharpe_dashboard.py / sharpe_dashboard_dhan.py).
_ACCENT       = "#1F4E79"   # portfolio line — navy, same as headers/buttons
_BENCH        = "#6B7A8D"   # benchmark line — muted slate, same as metric labels
_GOOD         = "#2E7D32"
_BAD          = "#C62828"
_TEXT_PRIMARY = "#1A1A2E"
_TEXT_MUTED   = "#6B7A8D"
_CARD_BG      = "#F5F7FA"
_BORDER       = "#E8ECF1"
_GRID         = "#EDF0F4"


def render_equity_curve(eq_df: pd.DataFrame, height: int = 460):
    """
    eq_df must have columns: date (datetime-like), portfolio_nav,
    benchmark_nav, n_held, invested_frac. `qty_weighted` (bool) is optional —
    rows recorded before the qty-weighting fix won't have it and are
    labelled "legacy" in the tooltip.
    """
    df = eq_df.sort_values("date").reset_index(drop=True)
    has_weighting_flag = "qty_weighted" in df.columns

    records = []
    for _, row in df.iterrows():
        if has_weighting_flag and pd.notna(row.get("qty_weighted")):
            method = "weighted" if bool(row["qty_weighted"]) else "fallback"
        else:
            method = "legacy"
        records.append({
            "date":       pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            "port":       round(float(row["portfolio_nav"]), 4),
            "bench":      round(float(row["benchmark_nav"]), 4),
            "n_held":     int(row["n_held"]) if pd.notna(row.get("n_held")) else None,
            "invested":   float(row["invested_frac"]) if pd.notna(row.get("invested_frac")) else None,
            "method":     method,
        })

    data_json = json.dumps(records)
    last = records[-1]
    port_delta = last["port"] - 100.0
    bench_delta = last["bench"] - 100.0

    html = f"""
<div id="eqroot" style="font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; box-sizing:border-box;">
  <style>
    #eqroot * {{ box-sizing: border-box; }}
    #eqroot .legend {{ display:flex; gap:18px; flex-wrap:wrap; margin-bottom:6px; }}
    #eqroot .legend-item {{ display:flex; align-items:center; gap:6px; font-size:12.5px; color:{_TEXT_MUTED}; }}
    #eqroot .legend-line {{ width:16px; height:0; border-top:2.5px solid; display:inline-block; }}
    #eqroot .legend-line.dashed {{ border-top-style:dashed; }}
    #eqroot .legend-dot {{ width:8px; height:8px; border-radius:50%; display:inline-block; }}
    #eqroot svg {{ display:block; width:100%; height:auto; overflow:visible; }}
    #eqroot .gridline {{ stroke:{_GRID}; stroke-width:1; }}
    #eqroot .baseline {{ stroke:{_BORDER}; stroke-width:1; }}
    #eqroot .axis-label {{ fill:{_TEXT_MUTED}; font-size:10.5px; }}
    #eqroot .series-path {{ fill:none; stroke-width:2.25; stroke-linejoin:round; stroke-linecap:round; }}
    #eqroot .crosshair {{ stroke:{_BORDER}; stroke-width:1; stroke-dasharray:3 3; pointer-events:none; }}
    #eqroot .hover-dot {{ stroke-width:2; pointer-events:none; }}
    #eqroot .fallback-dot {{ pointer-events:none; }}
    #eqroot .tooltip {{
      position:absolute; background:#ffffff; border:1px solid {_BORDER}; border-radius:8px;
      padding:9px 11px; font-size:12px; pointer-events:none; box-shadow:0 6px 20px rgba(20,30,50,0.12);
      min-width:150px; display:none; z-index:10;
    }}
    #eqroot .t-date {{ color:{_TEXT_MUTED}; font-size:11px; margin-bottom:6px; }}
    #eqroot .t-row {{ display:flex; align-items:center; justify-content:space-between; gap:14px; margin:3px 0; }}
    #eqroot .t-row .k {{ display:flex; align-items:center; gap:6px; color:{_TEXT_MUTED}; }}
    #eqroot .t-key {{ width:12px; height:0; border-top:2.5px solid; display:inline-block; }}
    #eqroot .t-key.dashed {{ border-top-style:dashed; }}
    #eqroot .t-row .v {{ font-weight:650; color:{_TEXT_PRIMARY}; font-variant-numeric:tabular-nums; }}
    #eqroot .t-note {{ margin-top:6px; padding-top:6px; border-top:1px solid {_GRID}; color:{_TEXT_MUTED}; font-size:10.5px; line-height:1.4; }}
    #eqroot .t-note.warn {{ color:#B8860B; }}
  </style>

  <div class="legend">
    <span class="legend-item"><span class="legend-line" style="border-color:{_ACCENT}"></span>Portfolio ({last["port"]:.2f}, {port_delta:+.2f}%)</span>
    <span class="legend-item"><span class="legend-line dashed" style="border-color:{_BENCH}"></span>Benchmark — NIFTY 500 ({last["bench"]:.2f}, {bench_delta:+.2f}%)</span>
    <span class="legend-item"><span class="legend-dot" style="background:#B8860B"></span>Equal-weight fallback / legacy day</span>
  </div>
  <div style="position:relative;">
    <svg id="eqsvg" viewBox="0 0 900 320" preserveAspectRatio="xMidYMid meet"></svg>
    <div class="tooltip" id="eqtooltip"></div>
  </div>
</div>

<script>
(function() {{
  const data = {data_json};
  const n = data.length;
  const svg = document.getElementById('eqsvg');
  const W = 900, H = 320;
  const M = {{ top: 14, right: 58, bottom: 26, left: 40 }};
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;

  const allVals = data.flatMap(d => [d.port, d.bench]);
  const yMin = Math.floor(Math.min(...allVals, 100) / 2) * 2 - 1;
  const yMax = Math.ceil(Math.max(...allVals) / 2) * 2 + 1;

  const xPos = i => M.left + (n === 1 ? 0 : (i / (n - 1)) * plotW);
  const yPos = v => M.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  const ns = 'http://www.w3.org/2000/svg';
  function el(tag, attrs) {{
    const e = document.createElementNS(ns, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }}

  function pathFor(key) {{
    let d = '';
    for (let i = 0; i < n; i++) {{
      d += (i === 0 ? 'M' : 'L') + xPos(i).toFixed(2) + ',' + yPos(data[i][key]).toFixed(2) + ' ';
    }}
    return d.trim();
  }}

  const yTicks = 5;
  for (let t = 0; t <= yTicks; t++) {{
    const v = yMin + (t / yTicks) * (yMax - yMin);
    const y = yPos(v);
    svg.appendChild(el('line', {{ x1: M.left, x2: W - M.right, y1: y, y2: y, class: Math.abs(v - 100) < 0.6 ? 'baseline' : 'gridline' }}));
    const lbl = el('text', {{ x: M.left - 8, y: y + 3, class: 'axis-label', 'text-anchor': 'end' }});
    lbl.textContent = v.toFixed(0);
    svg.appendChild(lbl);
  }}

  const xTickCount = Math.min(7, n);
  for (let t = 0; t < xTickCount; t++) {{
    const i = Math.round((t / Math.max(xTickCount - 1, 1)) * (n - 1));
    const lbl = el('text', {{ x: xPos(i), y: H - M.bottom + 16, class: 'axis-label', 'text-anchor': 'middle' }});
    const d = new Date(data[i].date);
    lbl.textContent = d.toLocaleDateString('en-US', {{ month: 'short', day: 'numeric' }});
    svg.appendChild(lbl);
  }}

  svg.appendChild(el('path', {{ d: pathFor('bench'), class: 'series-path', stroke: '{_BENCH}', 'stroke-dasharray': '6 4' }}));
  svg.appendChild(el('path', {{ d: pathFor('port'), class: 'series-path', stroke: '{_ACCENT}' }}));

  // flag non-weighted days with a small dot on the portfolio line
  data.forEach((d, i) => {{
    if (d.method !== 'weighted') {{
      svg.appendChild(el('circle', {{ cx: xPos(i), cy: yPos(d.port), r: 2.75, fill: '#B8860B', class: 'fallback-dot' }}));
    }}
  }});

  function endMark(key, color, label, dy) {{
    const cx = xPos(n - 1), cy = yPos(data[n - 1][key]);
    svg.appendChild(el('circle', {{ cx, cy, r: 4.5, fill: color, stroke: '#ffffff', 'stroke-width': 2 }}));
    const t = el('text', {{ x: cx + 8, y: cy + dy, class: 'axis-label', fill: color, 'font-weight': 650 }});
    t.textContent = label;
    svg.appendChild(t);
  }}
  endMark('port', '{_ACCENT}', data[n-1].port.toFixed(1), -8);
  endMark('bench', '{_BENCH}', data[n-1].bench.toFixed(1), 14);

  const hit = el('rect', {{ x: M.left, y: M.top, width: plotW, height: plotH, fill: 'transparent' }});
  svg.appendChild(hit);
  const crosshair = el('line', {{ class: 'crosshair', y1: M.top, y2: M.top + plotH, visibility: 'hidden' }});
  svg.appendChild(crosshair);
  const dotPort = el('circle', {{ r: 4, fill: '{_ACCENT}', stroke: '#ffffff', class: 'hover-dot', visibility: 'hidden' }});
  const dotBench = el('circle', {{ r: 4, fill: '{_BENCH}', stroke: '#ffffff', class: 'hover-dot', visibility: 'hidden' }});
  svg.appendChild(dotPort); svg.appendChild(dotBench);

  const tooltip = document.getElementById('eqtooltip');
  const wrap = tooltip.parentElement;

  function fmtDate(s) {{
    return new Date(s).toLocaleDateString('en-US', {{ weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }});
  }}

  function onMove(evt) {{
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX; pt.y = evt.clientY;
    const loc = pt.matrixTransform(svg.getScreenCTM().inverse());
    let i = Math.round(((loc.x - M.left) / plotW) * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    const row = data[i];
    const x = xPos(i);
    crosshair.setAttribute('x1', x); crosshair.setAttribute('x2', x); crosshair.setAttribute('visibility', 'visible');
    dotPort.setAttribute('cx', x); dotPort.setAttribute('cy', yPos(row.port)); dotPort.setAttribute('visibility', 'visible');
    dotBench.setAttribute('cx', x); dotBench.setAttribute('cy', yPos(row.bench)); dotBench.setAttribute('visibility', 'visible');

    let rowsHtml = `<div class="t-date"></div>`;
    rowsHtml += `<div class="t-row"><span class="k"><span class="t-key" style="border-color:{_ACCENT}"></span><span class="l1"></span></span><span class="v v1"></span></div>`;
    rowsHtml += `<div class="t-row"><span class="k"><span class="t-key dashed" style="border-color:{_BENCH}"></span><span class="l2"></span></span><span class="v v2"></span></div>`;
    tooltip.innerHTML = rowsHtml;
    tooltip.querySelector('.t-date').textContent = fmtDate(row.date);
    tooltip.querySelector('.l1').textContent = 'Portfolio';
    tooltip.querySelector('.v1').textContent = row.port.toFixed(2);
    tooltip.querySelector('.l2').textContent = 'Benchmark';
    tooltip.querySelector('.v2').textContent = row.bench.toFixed(2);

    const meta = document.createElement('div');
    meta.style.cssText = 'margin-top:4px; font-size:11px; color:{_TEXT_MUTED};';
    meta.textContent = (row.n_held != null ? row.n_held + ' held' : '') +
      (row.invested != null ? '  ·  ' + (row.invested * 100).toFixed(0) + '% invested' : '');
    tooltip.appendChild(meta);

    if (row.method === 'fallback') {{
      const note = document.createElement('div');
      note.className = 't-note warn';
      note.textContent = 'Equal-weight fallback — a held ticker was missing a synced qty that day.';
      tooltip.appendChild(note);
    }} else if (row.method === 'legacy') {{
      const note = document.createElement('div');
      note.className = 't-note';
      note.textContent = 'Recorded before the qty-weighting fix (equal-weight).';
      tooltip.appendChild(note);
    }}

    const wrapRect = wrap.getBoundingClientRect();
    const svgRect = svg.getBoundingClientRect();
    const scaleX = svgRect.width / W;
    let left = (x * scaleX) + (svgRect.left - wrapRect.left) + 14;
    if (left + 175 > wrapRect.width) left = (x * scaleX) + (svgRect.left - wrapRect.left) - 175;
    tooltip.style.left = Math.max(0, left) + 'px';
    tooltip.style.top = '4px';
    tooltip.style.display = 'block';
  }}
  function onLeave() {{
    crosshair.setAttribute('visibility', 'hidden');
    dotPort.setAttribute('visibility', 'hidden');
    dotBench.setAttribute('visibility', 'hidden');
    tooltip.style.display = 'none';
  }}
  hit.addEventListener('pointermove', onMove);
  hit.addEventListener('pointerleave', onLeave);
}})();
</script>
"""
    components.html(html, height=height, scrolling=False)
