/*
 * Generic click-to-sort table headers. Applies to every <table class="data-table">
 * on the page -- no per-template wiring needed beyond that one class, which every
 * table in this app already uses.
 *
 * Re-scans on DOMContentLoaded (first full page load) and on htmx:afterSettle
 * (any HTMX-swapped content, e.g. Full Rankings' filtered table, Tradelog/Cash
 * Ledger after an add/edit/delete) so newly-inserted tables get sortable headers
 * too, without any table needing its own re-init call.
 */
(function () {
  const CELL_SELECTOR = "td";

  // "Rs 1,23,456.78" / "+3.88%" / "-2.81%" / "1,234" -> 123456.78 / 3.88 / -2.81 / 1234.
  // Falls back to a parsed date (so date columns sort chronologically, not
  // alphabetically), then to lowercased text. Blank/placeholder cells ("—", "")
  // always sort to the end, regardless of direction -- common spreadsheet convention.
  function sortKey(text) {
    const raw = (text || "").trim();
    if (raw === "" || raw === "—") return { blank: true };

    const numeric = raw.replace(/^Rs\s*/i, "").replace(/[,%]/g, "").replace(/^\+/, "");
    const num = parseFloat(numeric);
    if (!isNaN(num) && /^-?[\d.]+$/.test(numeric.trim())) {
      return { blank: false, num };
    }

    const asDate = Date.parse(raw);
    if (!isNaN(asDate) && /\d{4}|\d{1,2}-[A-Za-z]{3}-\d{2}/.test(raw)) {
      return { blank: false, num: asDate };
    }

    return { blank: false, str: raw.toLowerCase() };
  }

  function compareKeys(a, b) {
    if (a.blank && b.blank) return 0;
    if (a.blank) return 1; // blanks always last
    if (b.blank) return -1;
    if (a.num !== undefined && b.num !== undefined) return a.num - b.num;
    if (a.num !== undefined) return -1; // numbers before text, arbitrary but stable
    if (b.num !== undefined) return 1;
    return a.str < b.str ? -1 : a.str > b.str ? 1 : 0;
  }

  function sortTableByColumn(table, colIndex, ascending) {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const rows = Array.from(tbody.rows);
    const keyed = rows.map((row) => ({
      row,
      key: sortKey(row.cells[colIndex] ? row.cells[colIndex].textContent : ""),
    }));
    keyed.sort((a, b) => (ascending ? compareKeys(a.key, b.key) : compareKeys(b.key, a.key)));
    keyed.forEach(({ row }) => tbody.appendChild(row));
  }

  function initTable(table) {
    if (table.dataset.sortableInit === "1") return;
    table.dataset.sortableInit = "1";
    const headerRow = table.tHead && table.tHead.rows[0];
    if (!headerRow) return;

    Array.from(headerRow.cells).forEach((th, colIndex) => {
      if (!th.textContent.trim()) return; // skip checkbox/action columns with no label
      th.classList.add("sortable-th");
      th.addEventListener("click", () => {
        const currentDir = th.getAttribute("data-sort-dir");
        const ascending = currentDir !== "asc";
        Array.from(headerRow.cells).forEach((h) => h.removeAttribute("data-sort-dir"));
        th.setAttribute("data-sort-dir", ascending ? "asc" : "desc");
        sortTableByColumn(table, colIndex, ascending);
      });
    });
  }

  function scan(root) {
    (root || document).querySelectorAll("table.data-table").forEach(initTable);
  }

  // This script is loaded from <head> (before <body> exists), so document.body is
  // null here -- both listeners must be registered only once the DOM is ready.
  document.addEventListener("DOMContentLoaded", () => {
    scan(document);
    document.body.addEventListener("htmx:afterSettle", (evt) => scan(evt.detail.target));
  });
})();
