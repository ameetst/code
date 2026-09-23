/*
 * Vanilla SVG equity-curve chart — hover crosshair, per-series tooltip, direct
 * end labels. Ported from equity_chart.py's inline HTML/JS component (same
 * visuals and interaction), as a reusable module instead of a Streamlit
 * components.html() blob: window.initEquityChart(containerId, records).
 */
(function () {
  const ACCENT = "#1F4E79";   // portfolio line — navy, same as headers/buttons
  const BENCH = "#6B7A8D";    // benchmark line — muted slate, same as metric labels
  const TEXT_MUTED = "#6B7A8D";

  const NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function fmtDate(s) {
    return new Date(s).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  }
  function fmtDelta(v) {
    return (v >= 0 ? "+" : "") + v.toFixed(2);
  }

  window.initEquityChart = function (containerId, data) {
    const root = document.getElementById(containerId);
    if (!root || !data || data.length === 0) return;

    const n = data.length;
    const last = data[n - 1];
    const portDelta = last.port - 100.0;
    const benchDelta = last.bench - 100.0;

    root.innerHTML = `
      <div class="eq-legend">
        <span class="eq-legend-item"><span class="eq-legend-line" style="border-color:${ACCENT}"></span>Portfolio (${last.port.toFixed(2)}, ${fmtDelta(portDelta)}%)</span>
        <span class="eq-legend-item"><span class="eq-legend-line dashed" style="border-color:${BENCH}"></span>Benchmark — NIFTY 500 (${last.bench.toFixed(2)}, ${fmtDelta(benchDelta)}%)</span>
        <span class="eq-legend-item"><span class="eq-legend-dot"></span>Equal-weight fallback / legacy day</span>
      </div>
      <div class="eq-wrap">
        <svg id="${containerId}-svg" class="eq-svg" viewBox="0 0 900 320" preserveAspectRatio="xMidYMid meet"></svg>
        <div class="eq-tooltip" id="${containerId}-tooltip"></div>
      </div>`;

    const svg = document.getElementById(`${containerId}-svg`);
    const W = 900, H = 320;
    const M = { top: 14, right: 58, bottom: 26, left: 40 };
    const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;

    const allVals = data.flatMap((d) => [d.port, d.bench]);
    const yMin = Math.floor(Math.min(...allVals, 100) / 2) * 2 - 1;
    const yMax = Math.ceil(Math.max(...allVals) / 2) * 2 + 1;
    const xPos = (i) => M.left + (n === 1 ? 0 : (i / (n - 1)) * plotW);
    const yPos = (v) => M.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

    function pathFor(key) {
      let d = "";
      for (let i = 0; i < n; i++) d += (i === 0 ? "M" : "L") + xPos(i).toFixed(2) + "," + yPos(data[i][key]).toFixed(2) + " ";
      return d.trim();
    }

    const yTicks = 5;
    for (let t = 0; t <= yTicks; t++) {
      const v = yMin + (t / yTicks) * (yMax - yMin);
      const y = yPos(v);
      svg.appendChild(el("line", { x1: M.left, x2: W - M.right, y1: y, y2: y, class: Math.abs(v - 100) < 0.6 ? "eq-baseline" : "eq-gridline" }));
      const lbl = el("text", { x: M.left - 8, y: y + 3, class: "eq-axis-label", "text-anchor": "end" });
      lbl.textContent = v.toFixed(0);
      svg.appendChild(lbl);
    }

    const xTickCount = Math.min(7, n);
    for (let t = 0; t < xTickCount; t++) {
      const i = Math.round((t / Math.max(xTickCount - 1, 1)) * (n - 1));
      const lbl = el("text", { x: xPos(i), y: H - M.bottom + 16, class: "eq-axis-label", "text-anchor": "middle" });
      lbl.textContent = new Date(data[i].date).toLocaleDateString("en-US", { month: "short", day: "numeric" });
      svg.appendChild(lbl);
    }

    svg.appendChild(el("path", { d: pathFor("bench"), class: "eq-series-path", stroke: BENCH, "stroke-dasharray": "6 4" }));
    svg.appendChild(el("path", { d: pathFor("port"), class: "eq-series-path", stroke: ACCENT }));

    // flag non-weighted days with a small dot on the portfolio line
    data.forEach((d, i) => {
      if (d.method !== "weighted") {
        svg.appendChild(el("circle", { cx: xPos(i), cy: yPos(d.port), r: 2.75, fill: "#B8860B", class: "eq-fallback-dot" }));
      }
    });

    function endMark(key, color, label, dy) {
      const cx = xPos(n - 1), cy = yPos(data[n - 1][key]);
      svg.appendChild(el("circle", { cx, cy, r: 4.5, fill: color, stroke: "#ffffff", "stroke-width": 2 }));
      const t = el("text", { x: cx + 8, y: cy + dy, class: "eq-axis-label", fill: color, "font-weight": 650 });
      t.textContent = label;
      svg.appendChild(t);
    }
    endMark("port", ACCENT, data[n - 1].port.toFixed(1), -8);
    endMark("bench", BENCH, data[n - 1].bench.toFixed(1), 14);

    const hit = el("rect", { x: M.left, y: M.top, width: plotW, height: plotH, fill: "transparent" });
    svg.appendChild(hit);
    const crosshair = el("line", { class: "eq-crosshair", y1: M.top, y2: M.top + plotH, visibility: "hidden" });
    svg.appendChild(crosshair);
    const dotPort = el("circle", { r: 4, fill: ACCENT, stroke: "#ffffff", class: "eq-hover-dot", visibility: "hidden" });
    const dotBench = el("circle", { r: 4, fill: BENCH, stroke: "#ffffff", class: "eq-hover-dot", visibility: "hidden" });
    svg.appendChild(dotPort);
    svg.appendChild(dotBench);

    const tooltip = document.getElementById(`${containerId}-tooltip`);
    const wrap = tooltip.parentElement;

    function onMove(evt) {
      const pt = svg.createSVGPoint();
      pt.x = evt.clientX;
      pt.y = evt.clientY;
      const loc = pt.matrixTransform(svg.getScreenCTM().inverse());
      let i = Math.round(((loc.x - M.left) / plotW) * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      const row = data[i];
      const x = xPos(i);
      crosshair.setAttribute("x1", x);
      crosshair.setAttribute("x2", x);
      crosshair.setAttribute("visibility", "visible");
      dotPort.setAttribute("cx", x);
      dotPort.setAttribute("cy", yPos(row.port));
      dotPort.setAttribute("visibility", "visible");
      dotBench.setAttribute("cx", x);
      dotBench.setAttribute("cy", yPos(row.bench));
      dotBench.setAttribute("visibility", "visible");

      let html = `<div class="eq-t-date">${fmtDate(row.date)}</div>`;
      html += `<div class="eq-t-row"><span class="eq-t-k"><span class="eq-t-key" style="border-color:${ACCENT}"></span>Portfolio</span><span class="eq-t-v">${row.port.toFixed(2)}</span></div>`;
      html += `<div class="eq-t-row"><span class="eq-t-k"><span class="eq-t-key dashed" style="border-color:${BENCH}"></span>Benchmark</span><span class="eq-t-v">${row.bench.toFixed(2)}</span></div>`;
      const metaParts = [];
      if (row.n_held != null) metaParts.push(row.n_held + " held");
      if (row.invested != null) metaParts.push((row.invested * 100).toFixed(0) + "% invested");
      if (metaParts.length) html += `<div class="eq-t-meta">${metaParts.join("  ·  ")}</div>`;
      if (row.method === "fallback") {
        html += `<div class="eq-t-note warn">Equal-weight fallback — a held ticker was missing a synced qty that day.</div>`;
      } else if (row.method === "legacy") {
        html += `<div class="eq-t-note">Recorded before the qty-weighting fix (equal-weight).</div>`;
      }
      tooltip.innerHTML = html;

      const wrapRect = wrap.getBoundingClientRect();
      const svgRect = svg.getBoundingClientRect();
      const scaleX = svgRect.width / W;
      let left = x * scaleX + (svgRect.left - wrapRect.left) + 14;
      if (left + 175 > wrapRect.width) left = x * scaleX + (svgRect.left - wrapRect.left) - 175;
      tooltip.style.left = Math.max(0, left) + "px";
      tooltip.style.top = "4px";
      tooltip.style.display = "block";
    }
    function onLeave() {
      crosshair.setAttribute("visibility", "hidden");
      dotPort.setAttribute("visibility", "hidden");
      dotBench.setAttribute("visibility", "hidden");
      tooltip.style.display = "none";
    }
    hit.addEventListener("pointermove", onMove);
    hit.addEventListener("pointerleave", onLeave);
  };
})();
