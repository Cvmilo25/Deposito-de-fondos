/* Dashboard: Nuevos depósitos de Fondos Mutuos — CMF Chile.
 * Lee data/historico_depositos.csv (generado por scripts/process.py) y
 * data/last_update.json, sin dependencias externas. */

const DATA_URL = "data/historico_depositos.csv";
const META_URL = "data/last_update.json";

const TIPO_ORDER = [
  "Fondo Mutuo",
  "Fondo Inversión No Rescatable",
  "Fondo Inversión Rescatable",
];
const TIPO_COLOR_VAR = {
  "Fondo Mutuo": "--series-1",
  "Fondo Inversión No Rescatable": "--series-2",
  "Fondo Inversión Rescatable": "--series-3",
};
const TIPO_BADGE_CLASS = {
  "Fondo Mutuo": "fm",
  "Fondo Inversión No Rescatable": "finr",
  "Fondo Inversión Rescatable": "fir",
};

const state = {
  rows: [],
  periodDays: 30,
  sortKey: "fecha_deposito",
  sortDir: "desc",
};

function parseCSV(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (c === "\r") {
      // skip
    } else {
      field += c;
    }
  }
  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.length > 1 || r[0] !== "");
}

function toRecords(csvText) {
  const rows = parseCSV(csvText);
  const header = rows[0];
  return rows.slice(1).map((r) => {
    const obj = {};
    header.forEach((h, i) => (obj[h] = r[i] ?? ""));
    return obj;
  });
}

function daysAgo(n) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - n);
  return d;
}

function parseISODate(s) {
  if (!s) return null;
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function fmtDateEs(s) {
  const d = parseISODate(s);
  if (!d) return "";
  return d.toLocaleDateString("es-CL");
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
  }
  (children || []).forEach((c) => node.appendChild(c));
  return node;
}

function populateSelect(select, values, currentValue) {
  const existing = new Set(Array.from(select.options).map((o) => o.value));
  values.forEach((v) => {
    if (!existing.has(v)) {
      select.appendChild(el("option", { value: v, text: v }));
    }
  });
  if (currentValue) select.value = currentValue;
}

function applyFilters(rows) {
  const admin = document.getElementById("f-admin").value;
  const tipo = document.getElementById("f-tipo").value;
  const nombre = document.getElementById("f-nombre").value.trim().toLowerCase();

  return rows.filter((r) => {
    if (admin && r.administradora !== admin) return false;
    if (tipo && r.tipo_fondo !== tipo) return false;
    if (nombre && !r.nombre_fondo.toLowerCase().includes(nombre)) return false;
    return true;
  });
}

function inPeriod(rows, days) {
  const cutoff = daysAgo(days);
  return rows.filter((r) => {
    const d = parseISODate(r.fecha_deposito);
    return d && d >= cutoff;
  });
}

function renderKPIs(filteredAll, periodRows) {
  document.getElementById("kpi-period-label").textContent =
    `Nuevos fondos depositados — últimos ${state.periodDays} días`;
  document.getElementById("kpi-admin-label").textContent =
    `Administradoras involucradas — últimos ${state.periodDays} días`;
  document.getElementById("kpi-nuevos").textContent = periodRows.length.toLocaleString("es-CL");
  document.getElementById("kpi-admins").textContent = new Set(
    periodRows.map((r) => r.administradora)
  ).size.toLocaleString("es-CL");
  document.getElementById("kpi-7d").textContent = inPeriod(filteredAll, 7).length.toLocaleString(
    "es-CL"
  );
  document.getElementById("kpi-30d").textContent = inPeriod(
    filteredAll,
    30
  ).length.toLocaleString("es-CL");
}

function renderChart(periodRows) {
  const container = document.getElementById("chart-tipo");
  container.innerHTML = "";
  const counts = {};
  TIPO_ORDER.forEach((t) => (counts[t] = 0));
  periodRows.forEach((r) => {
    counts[r.tipo_fondo] = (counts[r.tipo_fondo] || 0) + 1;
  });
  const max = Math.max(1, ...Object.values(counts));

  TIPO_ORDER.forEach((tipo) => {
    const value = counts[tipo] || 0;
    const pct = Math.max(2, Math.round((value / max) * 100));
    const colorVar = TIPO_COLOR_VAR[tipo];
    const row = el("div", { class: "bar-row" }, [
      el("div", { class: "bar-label", text: tipo }),
      el("div", { class: "bar-track" }, [
        el("div", {
          class: "bar-fill",
          style: `width:${pct}%; background: var(${colorVar});`,
        }),
      ]),
      el("div", { class: "bar-value", text: value.toLocaleString("es-CL") }),
    ]);
    container.appendChild(row);
  });
}

function renderTable(rows) {
  const sorted = [...rows].sort((a, b) => {
    const key = state.sortKey === "_reglamento" ? "reglamento_url" : state.sortKey;
    let av = a[key] || "";
    let bv = b[key] || "";
    const th = document.querySelector(`th[data-key="${state.sortKey}"]`);
    if (th && th.dataset.type === "num") {
      av = Number(av) || 0;
      bv = Number(bv) || 0;
    }
    if (av < bv) return state.sortDir === "asc" ? -1 : 1;
    if (av > bv) return state.sortDir === "asc" ? 1 : -1;
    return 0;
  });

  const tbody = document.getElementById("tbl-body");
  tbody.innerHTML = "";
  const MAX_ROWS = 500;
  sorted.slice(0, MAX_ROWS).forEach((r) => {
    const badgeClass = TIPO_BADGE_CLASS[r.tipo_fondo] || "fm";
    const reg = r.reglamento_url
      ? el("a", {
          class: "reg-link",
          href: r.reglamento_url,
          target: "_blank",
          rel: "noopener",
          text: "Ver reglamento",
        })
      : el("span", { class: "no-reg", text: "No disponible" });

    const tr = el("tr", null, [
      el("td", { text: fmtDateEs(r.fecha_deposito) }),
      el("td", { text: r.administradora }),
      el("td", { text: r.nombre_fondo }),
      el("td", { class: "num", text: r.run_fondo }),
      el("td", null, [el("span", { class: `badge ${badgeClass}`, text: r.tipo_fondo })]),
      el("td", null, [reg]),
    ]);
    tbody.appendChild(tr);
  });

  const countLabel = document.getElementById("row-count");
  countLabel.textContent =
    sorted.length > MAX_ROWS
      ? `Mostrando ${MAX_ROWS} de ${sorted.length.toLocaleString("es-CL")} registros — refina los filtros para ver el resto`
      : `${sorted.length.toLocaleString("es-CL")} registros`;

  document.querySelectorAll("thead th").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.key === state.sortKey);
    th.dataset.dir = state.sortDir === "asc" ? "▲" : "▼";
  });
}

function render() {
  const filteredAll = applyFilters(state.rows);
  const periodRows = inPeriod(filteredAll, state.periodDays);
  renderKPIs(filteredAll, periodRows);
  renderChart(periodRows);
  renderTable(periodRows);
}

function wireEvents() {
  document.getElementById("period-toggle").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-days]");
    if (!btn) return;
    state.periodDays = Number(btn.dataset.days);
    document
      .querySelectorAll("#period-toggle button")
      .forEach((b) => b.classList.toggle("active", b === btn));
    render();
  });

  ["f-admin", "f-tipo", "f-nombre"].forEach((id) => {
    document.getElementById(id).addEventListener("input", render);
    document.getElementById(id).addEventListener("change", render);
  });

  document.getElementById("f-clear").addEventListener("click", () => {
    ["f-admin", "f-tipo", "f-nombre"].forEach((id) => {
      document.getElementById(id).value = "";
    });
    render();
  });

  document.querySelectorAll("thead th[data-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (state.sortKey === key) {
        state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      } else {
        state.sortKey = key;
        state.sortDir = key === "fecha_deposito" ? "desc" : "asc";
      }
      render();
    });
  });
}

async function init() {
  wireEvents();
  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    state.rows = toRecords(text);
  } catch (err) {
    document.getElementById("last-update").textContent =
      "No se pudo cargar data/historico_depositos.csv";
    console.error(err);
    return;
  }

  const admins = [...new Set(state.rows.map((r) => r.administradora))].sort((a, b) =>
    a.localeCompare(b, "es")
  );
  populateSelect(document.getElementById("f-admin"), admins);
  populateSelect(document.getElementById("f-tipo"), TIPO_ORDER);

  try {
    const metaRes = await fetch(META_URL, { cache: "no-store" });
    if (metaRes.ok) {
      const meta = await metaRes.json();
      document.getElementById("last-update").textContent =
        `Actualizado: ${meta.actualizado_en} · ${meta.total_registros.toLocaleString("es-CL")} registros en histórico`;
    } else {
      document.getElementById("last-update").textContent = "";
    }
  } catch {
    document.getElementById("last-update").textContent = "";
  }

  render();
}

init();
