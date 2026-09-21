// ETF Momentum Strategy -- dashboard behaviour.
// The page is server-rendered; this file handles tabs, client-side sort/filter,
// row-selection -> trade-form prefill, and the JSON actions under /api.
(() => {
  "use strict";
  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const bootEl = $("#boot");
  const BOOT = bootEl ? JSON.parse(bootEl.textContent) : { prices: {}, tickers: [] };

  // ── Toasts (a message can be queued across the post-action reload) ──
  let toastTimer;
  function toast(msg, kind = "ok", sticky = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.className = `show ${kind}`;
    clearTimeout(toastTimer);
    if (!sticky) toastTimer = setTimeout(() => el.classList.remove("show"), kind === "err" ? 8000 : 3500);
  }
  function flashNext(msg, kind = "ok") {
    try { sessionStorage.setItem("flash", JSON.stringify({ msg, kind })); } catch (_) { /* storage blocked */ }
  }
  try {
    const f = JSON.parse(sessionStorage.getItem("flash") || "null");
    if (f) { sessionStorage.removeItem("flash"); toast(f.msg, f.kind); }
  } catch (_) { /* ignore */ }

  // ── API helper ──
  async function api(method, url, body) {
    const opts = { method, headers: { "X-Requested-With": "fetch" } };
    if (body instanceof FormData) {
      opts.body = body;
    } else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try { res = await fetch(url, opts); } catch (_) { throw new Error("Could not reach the server."); }
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
    if (!res.ok || !data || !data.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }

  // Run an action from a button: busy state, then reload the page on success.
  async function act(btn, fn) {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add("busy");
    if (btn.dataset.busy) toast(btn.dataset.busy, "busy", true);
    try {
      const data = await fn();
      flashNext(data.message);
      location.reload();
    } catch (e) {
      toast(e.message, "err");
      btn.disabled = false;
      btn.classList.remove("busy");
    }
  }

  // ── Tabs (current tab lives in the URL hash so reloads stay put) ──
  const TABS = ["recommendation", "rankings", "config", "tradelog"];
  function showTab(name) {
    if (!TABS.includes(name)) name = TABS[0];
    $$(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
    $$(".tab-panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  }
  $$(".tab-btn").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  if ($$(".tab-btn").length) showTab(location.hash.slice(1));

  // ── Sortable tables ──
  $$("table.sortable-table").forEach(table => {
    const ths = $$("thead th", table);
    ths.forEach((th, i) => {
      if (!th.classList.contains("sortable")) return;
      th.addEventListener("click", () => {
        const dir = th.dataset.dir === "asc" ? "desc" : "asc";
        ths.forEach(t => delete t.dataset.dir);
        th.dataset.dir = dir;
        const key = row => {
          const cell = row.children[i];
          const v = (cell.dataset.v ?? cell.textContent).trim();
          if (v === "" || v === "—") return null;
          const n = Number(v);
          return Number.isFinite(n) ? n : v.toLowerCase();
        };
        const rows = $$("tbody tr", table).sort((a, b) => {
          const x = key(a), y = key(b);
          if (x === null || y === null) return (x === null) - (y === null);   // blanks always last
          let c;
          if (typeof x === typeof y) c = typeof x === "number" ? x - y : x.localeCompare(y);
          else c = typeof x === "number" ? -1 : 1;
          return dir === "asc" ? c : -c;
        });
        rows.forEach(r => table.tBodies[0].appendChild(r));
        if (table.id === "rank-table") applyRankFilters();
      });
    });
  });

  // ── Full Rankings filters (client-side) ──
  function applyRankFilters() {
    const table = $("#rank-table");
    if (!table) return;
    const screen = $("#f-screen").value, sector = $("#f-sector").value, topn = Number($("#f-topn").value);
    $("#f-topn-val").textContent = topn;
    let visible = 0;
    $$("tbody tr", table).forEach(tr => {
      let ok = (screen === "all" || (screen === "pass") === (tr.dataset.screen === "1"))
            && (!sector || tr.dataset.sector === sector);
      if (ok && visible >= topn) ok = false;
      if (ok) visible++;
      tr.hidden = !ok;
    });
    $("#f-shown").textContent = visible;
  }
  ["#f-screen", "#f-sector", "#f-topn"].forEach(s => $(s)?.addEventListener("input", applyRankFilters));
  applyRankFilters();

  // ── Trade form: selecting a row anywhere pre-fills it ──
  const tlTicker = $("#tl-ticker"), tlQty = $("#tl-qty"), tlPrice = $("#tl-price");
  function priceOf(ticker) { return BOOT.prices[ticker]; }
  function prefill(ticker, { qty, price } = {}) {
    if (!tlTicker || !ticker || ticker === "CASH") return;
    tlTicker.value = ticker;
    if (qty) tlQty.value = qty;
    const px = price ?? priceOf(ticker);
    if (px !== undefined) tlPrice.value = px;
    toast(`Selected ${ticker} — filled in Log New Transaction (Tradelog & MTM tab).`);
  }
  $$("table.selectable-table").forEach(table => {
    table.tBodies[0].addEventListener("click", e => {
      const tr = e.target.closest("tr.selectable");
      if (!tr) return;
      const wasSelected = tr.classList.contains("selected");
      $$("tr.selected", table).forEach(r => r.classList.remove("selected"));
      if (wasSelected) return;
      tr.classList.add("selected");
      prefill(tr.dataset.ticker, { qty: tr.dataset.qty, price: tr.dataset.price });
    });
  });
  tlTicker?.addEventListener("input", () => {
    const t = tlTicker.value.trim().toUpperCase();
    if (priceOf(t) !== undefined) tlPrice.value = priceOf(t);      // exact ticker chosen -> its latest price
  });

  // ── Record a transaction ──
  $("#trade-form")?.addEventListener("submit", e => {
    e.preventDefault();
    act(e.submitter, () => api("POST", "/api/trades", {
      ticker:   tlTicker.value.trim().toUpperCase(),
      action:   $("input[name=tl-action]:checked").value,
      date:     $("#tl-date").value,
      quantity: tlQty.value,
      price:    tlPrice.value,
    }));
  });

  // ── Edit a transaction (dialog) ──
  const dlg = $("#edit-dialog");
  let editId = null;
  $$(".tx-edit").forEach(btn => btn.addEventListener("click", () => {
    const d = btn.closest("tr").dataset;
    editId = d.id;
    $("#ed-ticker").value = d.ticker;
    $(`input[name=ed-action][value=${d.action}]`).checked = true;
    $("#ed-date").value = d.date;
    $("#ed-qty").value = d.qty;
    $("#ed-price").value = d.price;
    dlg.showModal();
  }));
  $("#ed-cancel")?.addEventListener("click", () => dlg.close());
  $("#edit-form")?.addEventListener("submit", e => {
    e.preventDefault();
    act(e.submitter, () => api("PUT", `/api/trades/${encodeURIComponent(editId)}`, {
      ticker:   $("#ed-ticker").value.trim().toUpperCase(),
      action:   $("input[name=ed-action]:checked").value,
      date:     $("#ed-date").value,
      quantity: $("#ed-qty").value,
      price:    $("#ed-price").value,
    }));
  });

  // ── Delete transactions ──
  const delBtn = $("#btn-delete");
  const checks = () => $$(".tx-check:checked");
  $$(".tx-check").forEach(c => c.addEventListener("change", () => {
    const n = checks().length;
    delBtn.disabled = n === 0;
    $("#tx-sel-count").textContent = n ? `${n} transaction(s) selected` : "No transactions selected";
  }));
  delBtn?.addEventListener("click", () => {
    const ids = checks().map(c => c.closest("tr").dataset.id);
    if (!ids.length || !confirm(`Permanently delete ${ids.length} transaction(s)?`)) return;
    act(delBtn, () => api("POST", "/api/trades/delete", { ids }));
  });

  // ── Live prices, rebalance, config ──
  $("#btn-refresh")?.addEventListener("click", e => act(e.currentTarget, () => api("POST", "/api/refresh-prices")));
  $("#btn-rebalance")?.addEventListener("click", e => act(e.currentTarget, () => api("POST", "/api/rebalance", {})));

  const cfgValues = () => Object.fromEntries($$("#cfg-form [name]").map(el => [el.name, el.value]));
  $("#btn-cfg-save")?.addEventListener("click", e => act(e.currentTarget, () => api("POST", "/api/config", cfgValues())));
  $("#btn-cfg-run")?.addEventListener("click", e => act(e.currentTarget, () => api("POST", "/api/rebalance", { config: cfgValues() })));

  // ── Data source ──
  const dsUpload = $("#ds-radios input[value=upload]");
  if (dsUpload) {
    const hadUpload = dsUpload.defaultChecked;
    $$("#ds-radios input").forEach(r => r.addEventListener("change", () => {
      const upload = dsUpload.checked;
      $("#ds-upload").hidden = !upload;
      $("#ds-default").hidden = upload;
      if (!upload && hadUpload) {
        api("POST", "/api/use-default").then(d => { flashNext(d.message); location.reload(); })
                                       .catch(err => toast(err.message, "err"));
      }
    }));
    $("#ds-file").addEventListener("change", ev => {
      const file = ev.target.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      toast("Uploading and validating file…", "busy", true);
      api("POST", "/api/upload", fd).then(d => { flashNext(d.message); location.reload(); })
                                    .catch(err => { toast(err.message, "err"); ev.target.value = ""; });
    });
  }
})();
