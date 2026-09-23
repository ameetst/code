/*
 * Generic multi-series SVG line chart — hover crosshair + tooltip, direct end labels.
 * Same visual language as equity_chart.js, generalized to N named series instead of a
 * fixed portfolio/benchmark pair. Used by the Regime Score Trend chart.
 *
 * window.initLineChart(containerId, records, seriesSpec)
 *   records:   [{date: "YYYY-MM-DD", <seriesKey>: number, ...}, ...]
 *   seriesSpec: [{key, label, color, dashed?: bool}, ...]
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function fmtDate(s) {
    return new Date(s).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  }

  window.initLineChart = function (containerId, data, seriesSpec) {
    const root = document.getElementById(containerId);
    if (!root || !data || data.length === 0) return;

    const n = data.length;
    const legendHtml = seriesSpec
      .map((s) => `<span class="lc-legend-item"><span class="lc-legend-line${s.dashed ? " dashed" : ""}" style="border-color:${s.color}"></span>${s.label}</span>`)
      .join("");

    root.innerHTML = `
      <div class="lc-legend">${legendHtml}</div>
      <div class="lc-wrap">
        <svg id="${containerId}-svg" class="lc-svg" viewBox="0 0 900 280" preserveAspectRatio="xMidYMid meet"></svg>
        <div class="lc-tooltip" id="${containerId}-tooltip"></div>
      </div>`;

    const svg = document.getElementById(`${containerId}-svg`);
    const W = 900, H = 280;
    const M = { top: 14, right: 70, bottom: 26, left: 40 };
    const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;

    const allVals = data.flatMap((d) => seriesSpec.map((s) => d[s.key]).filter((v) => v != null));
    const yMin = Math.min(...allVals, 0);
    const yMax = Math.max(...allVals, 0.1);
    const pad = (yMax - yMin) * 0.08 || 0.05;
    const yLo = yMin - pad, yHi = yMax + pad;

    const xPos = (i) => M.left + (n === 1 ? 0 : (i / (n - 1)) * plotW);
    const yPos = (v) => M.top + plotH - ((v - yLo) / (yHi - yLo)) * plotH;

    function pathFor(key) {
      let d = "";
      let started = false;
      for (let i = 0; i < n; i++) {
        const v = data[i][key];
        if (v == null) continue;
        d += (!started ? "M" : "L") + xPos(i).toFixed(2) + "," + yPos(v).toFixed(2) + " ";
        started = true;
      }
      return d.trim();
    }

    const yTicks = 5;
    for (let t = 0; t <= yTicks; t++) {
      const v = yLo + (t / yTicks) * (yHi - yLo);
      const y = yPos(v);
      svg.appendChild(el("line", { x1: M.left, x2: W - M.right, y1: y, y2: y, class: "lc-gridline" }));
      const lbl = el("text", { x: M.left - 8, y: y + 3, class: "lc-axis-label", "text-anchor": "end" });
      lbl.textContent = v.toFixed(2);
      svg.appendChild(lbl);
    }

    const xTickCount = Math.min(7, n);
    for (let t = 0; t < xTickCount; t++) {
      const i = Math.round((t / Math.max(xTickCount - 1, 1)) * (n - 1));
      const lbl = el("text", { x: xPos(i), y: H - M.bottom + 16, class: "lc-axis-label", "text-anchor": "middle" });
      lbl.textContent = new Date(data[i].date).toLocaleDateString("en-US", { month: "short", day: "numeric" });
      svg.appendChild(lbl);
    }

    seriesSpec.forEach((s) => {
      svg.appendChild(el("path", {
        d: pathFor(s.key), class: "lc-series-path", stroke: s.color,
        ...(s.dashed ? { "stroke-dasharray": "6 4" } : {}),
      }));
    });

    seriesSpec.forEach((s, idx) => {
      let lastIdx = -1;
      for (let i = n - 1; i >= 0; i--) {
        if (data[i][s.key] != null) { lastIdx = i; break; }
      }
      if (lastIdx < 0) return;
      const cx = xPos(lastIdx), cy = yPos(data[lastIdx][s.key]);
      svg.appendChild(el("circle", { cx, cy, r: 4, fill: s.color, stroke: "#ffffff", "stroke-width": 2 }));
      const t = el("text", { x: cx + 8, y: cy + (idx % 2 === 0 ? -6 : 12), class: "lc-axis-label", fill: s.color, "font-weight": 650 });
      t.textContent = data[lastIdx][s.key].toFixed(2);
      svg.appendChild(t);
    });

    const hit = el("rect", { x: M.left, y: M.top, width: plotW, height: plotH, fill: "transparent" });
    svg.appendChild(hit);
    const crosshair = el("line", { class: "lc-crosshair", y1: M.top, y2: M.top + plotH, visibility: "hidden" });
    svg.appendChild(crosshair);
    const dots = seriesSpec.map((s) =>
      el("circle", { r: 4, fill: s.color, stroke: "#ffffff", class: "lc-hover-dot", visibility: "hidden" })
    );
    dots.forEach((d) => svg.appendChild(d));

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

      let html = `<div class="lc-t-date">${fmtDate(row.date)}</div>`;
      seriesSpec.forEach((s, idx) => {
        const v = row[s.key];
        dots[idx].setAttribute("visibility", v == null ? "hidden" : "visible");
        if (v != null) {
          dots[idx].setAttribute("cx", x);
          dots[idx].setAttribute("cy", yPos(v));
          html += `<div class="lc-t-row"><span class="lc-t-k"><span class="lc-t-key${s.dashed ? " dashed" : ""}" style="border-color:${s.color}"></span>${s.label}</span><span class="lc-t-v">${v.toFixed(3)}</span></div>`;
        }
      });
      tooltip.innerHTML = html;

      const wrapRect = wrap.getBoundingClientRect();
      const svgRect = svg.getBoundingClientRect();
      const scaleX = svgRect.width / W;
      let left = x * scaleX + (svgRect.left - wrapRect.left) + 14;
      if (left + 190 > wrapRect.width) left = x * scaleX + (svgRect.left - wrapRect.left) - 190;
      tooltip.style.left = Math.max(0, left) + "px";
      tooltip.style.top = "4px";
      tooltip.style.display = "block";
    }
    function onLeave() {
      crosshair.setAttribute("visibility", "hidden");
      dots.forEach((d) => d.setAttribute("visibility", "hidden"));
      tooltip.style.display = "none";
    }
    hit.addEventListener("pointermove", onMove);
    hit.addEventListener("pointerleave", onLeave);
  };
})();
