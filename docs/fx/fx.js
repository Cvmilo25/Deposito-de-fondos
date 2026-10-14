/* Pronóstico USD/CLP a 21 días hábiles.
 * Lee los archivos que genera `python -m fx.run_daily` en docs/data/fx/, sin dependencias externas. */

const BASE = "../data/fx/";
const MODEL_ORDER = ["ensemble", "ridge", "lgbm", "tvp", "ar", "rw_drift", "rw"];
const MAIN_MODELS = ["ensemble", "ridge", "lgbm", "tvp"];
const SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--text-secondary", "--text-muted"];

const state = { metrics: null, bt: [], rolling: [], betas: [], log: [], model: "ensemble", win: "test_5y", drv: "ridge" };

/* ---------------- utilidades ---------------- */
function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/);
  const head = lines.shift().split(",");
  return lines.map((l) => {
    const cells = l.split(",");
    const o = {};
    head.forEach((h, i) => {
      const v = cells[i];
      o[h] = v === "" || v === undefined ? null : isNaN(v) || /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : Number(v);
    });
    return o;
  });
}
const clp = (v, d = 1) => (v == null ? "–" : "$" + v.toLocaleString("es-CL", { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (v, d = 1) => (v == null ? "–" : v.toLocaleString("es-CL", { minimumFractionDigits: d, maximumFractionDigits: d }) + " %");
const num = (v, d = 3) => (v == null ? "–" : v.toLocaleString("es-CL", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtDate = (s) => { const [y, m, d] = s.split("-"); return `${d}-${m}-${y}`; };
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const label = (m) => (state.metrics?.meta?.etiquetas_modelos?.[m]) || m;
const featLabel = (f) => (state.metrics?.meta?.etiquetas_variables?.[f]) || f;
function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v;
    else if (k === "class") e.className = v;
    else e.setAttribute(k, v);
  }
  children.forEach((c) => e.appendChild(c));
  return e;
}
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

/* ---------------- gráfico de líneas genérico ---------------- */
function niceTicks(min, max, n = 5) {
  const step0 = (max - min) / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => k >= step0) || step0;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

/** series: [{key, points:[[Date, value]], cls|color, dash}], band: {points:[[Date, lo, hi]]}, refY */
function lineChart(container, { series, band, refY, yFmt = (v) => v.toLocaleString("es-CL"), tip, legend }) {
  container.innerHTML = "";
  if (legend) container.appendChild(legend);
  const W = container.clientWidth || 800;
  const H = (container.clientHeight || 300) - (legend ? legend.offsetHeight + 6 : 0);
  const m = { l: 56, r: 12, t: 10, b: 26 };
  const all = series.flatMap((x) => x.points);
  if (band) band.points.forEach((p) => { all.push([p[0], p[1]]); all.push([p[0], p[2]]); });
  if (!all.length) return;
  const xs = all.map((p) => p[0].getTime());
  const ys = all.map((p) => p[1]).filter((v) => v != null && isFinite(v));
  if (refY != null) ys.push(refY);
  let [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const pad = (y1 - y0) * 0.06 || 1; y0 -= pad; y1 += pad;
  const X = (t) => m.l + ((t - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", style: `height:${H}px` });

  niceTicks(y0, y1).forEach((v) => {
    svg.appendChild(s("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: "grid" }));
    const t = s("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", class: "axis" }); t.textContent = yFmt(v); svg.appendChild(t);
  });
  const y0d = new Date(x0).getFullYear(), y1d = new Date(x1).getFullYear();
  const yearStep = Math.max(1, Math.ceil((y1d - y0d + 1) / 8));
  for (let yr = y0d; yr <= y1d + 1; yr += yearStep) {
    const t0 = new Date(yr, 0, 1).getTime();
    if (t0 < x0 || t0 > x1) continue;
    svg.appendChild(s("line", { x1: X(t0), x2: X(t0), y1: m.t, y2: H - m.b, class: "grid" }));
    const t = s("text", { x: X(t0), y: H - 8, "text-anchor": "middle", class: "axis" }); t.textContent = yr; svg.appendChild(t);
  }
  svg.appendChild(s("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, class: "base" }));
  if (refY != null) svg.appendChild(s("line", { x1: m.l, x2: W - m.r, y1: Y(refY), y2: Y(refY), class: "ref" }));

  if (band && band.points.length) {
    const up = band.points.map((p) => `${X(p[0].getTime())},${Y(p[2])}`);
    const dn = band.points.slice().reverse().map((p) => `${X(p[0].getTime())},${Y(p[1])}`);
    svg.appendChild(s("polygon", { points: up.concat(dn).join(" "), class: "band" }));
  }
  series.forEach((sr) => {
    if (!sr.points.length) return;
    const d = sr.points.map((p, i) => `${i ? "L" : "M"}${X(p[0].getTime()).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    const attrs = { d, class: sr.cls || "" };
    if (sr.color) Object.assign(attrs, { fill: "none", stroke: sr.color, "stroke-width": 1.4 });
    svg.appendChild(s("path", attrs));
  });

  // Tooltip con línea vertical.
  const hover = s("line", { y1: m.t, y2: H - m.b, class: "hover-line", visibility: "hidden" });
  svg.appendChild(hover);
  container.appendChild(svg);
  const tipEl = el("div", { class: "chart-tip" });
  container.appendChild(tipEl);
  const ref = series[0].points;
  svg.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const t = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
    let best = 0, bd = Infinity;
    ref.forEach((p, i) => { const d = Math.abs(p[0].getTime() - t); if (d < bd) { bd = d; best = i; } });
    const date = ref[best][0];
    hover.setAttribute("x1", X(date.getTime())); hover.setAttribute("x2", X(date.getTime()));
    hover.setAttribute("visibility", "visible");
    tipEl.innerHTML = tip(date);
    tipEl.style.display = "block";
    const left = (X(date.getTime()) / W) * r.width;
    tipEl.style.left = Math.min(left + 12, r.width - tipEl.offsetWidth - 4) + "px";
    tipEl.style.top = svg.offsetTop + 8 + "px";
  });
  svg.addEventListener("mouseleave", () => { hover.setAttribute("visibility", "hidden"); tipEl.style.display = "none"; });
}

/* ---------------- render ---------------- */
function renderKPIs() {
  const h = state.metrics.hoy, e = h.modelos.ensemble;
  document.getElementById("kpi-spot").textContent = clp(h.dolar_observado_hoy, 2);
  document.getElementById("kpi-spot-date").textContent = "Fecha " + fmtDate(h.fecha_origen);
  const kEns = document.getElementById("kpi-ens");
  kEns.textContent = clp(e.pronostico, 1);
  const sub = document.getElementById("kpi-ens-sub");
  sub.textContent = `${e.cambio_pct >= 0 ? "▲" : "▼"} ${pct(Math.abs(e.cambio_pct), 2)} · para el ${fmtDate(h.fecha_objetivo_estimada)} (aprox.)`;
  sub.className = "sub " + (e.cambio_pct >= 0 ? "up" : "down");
  document.getElementById("kpi-range").textContent = e.lo80 ? `${clp(e.lo80, 0)} – ${clp(e.hi80, 0)}` : "–";
  document.getElementById("kpi-range95").textContent = e.lo95 ? `95 %: ${clp(e.lo95, 0)} – ${clp(e.hi95, 0)}` : "";
  const tu = state.metrics.metricas.test_5y.modelos.ensemble.theil_u;
  const kt = document.getElementById("kpi-theil");
  kt.textContent = num(tu, 3);
  kt.className = "value " + (tu < 1 ? "down" : "up");
  document.getElementById("last-update").textContent =
    `Actualizado: ${state.metrics.meta.actualizado_en} · último dato ${fmtDate(state.metrics.meta.ultimo_dato)}`;
}

function renderToday() {
  const tb = document.querySelector("#tbl-today tbody");
  tb.innerHTML = "";
  MODEL_ORDER.forEach((m) => {
    const r = state.metrics.hoy.modelos[m];
    const tr = el("tr", { class: m === "ensemble" ? "main" : MAIN_MODELS.includes(m) ? "" : "bench" }, [
      el("td", { text: label(m) }),
      el("td", { class: "num", text: clp(r.pronostico, 1) }),
      el("td", { class: "num " + (r.cambio_pct >= 0 ? "up" : "down"), text: (r.cambio_pct >= 0 ? "+" : "") + pct(r.cambio_pct, 2) }),
      el("td", { class: "num", text: r.lo80 ? `${clp(r.lo80, 0)} – ${clp(r.hi80, 0)}` : "–" }),
      el("td", { class: "num", text: r.lo95 ? `${clp(r.lo95, 0)} – ${clp(r.hi95, 0)}` : "–" }),
    ]);
    tb.appendChild(tr);
  });
}

function renderMain() {
  const m = state.model;
  const w = state.metrics.metricas.test_5y;
  const from = new Date(w.desde + "T00:00:00");
  const rows = state.bt.filter((r) => new Date(r.fecha_objetivo + "T00:00:00") >= from);
  const real = rows.filter((r) => r.real != null);
  const fut = rows.filter((r) => r.real == null);
  const D = (r) => new Date(r.fecha_objetivo + "T00:00:00");
  const lastReal = real[real.length - 1];
  const futurePts = lastReal ? [[D(lastReal), lastReal.real]].concat(fut.map((r) => [D(r), r[`pred_${m}`]])) : [];
  lineChart(document.getElementById("chart-main"), {
    series: [
      { key: "real", points: real.map((r) => [D(r), r.real]), cls: "real" },
      { key: "pred", points: real.map((r) => [D(r), r[`pred_${m}`]]), cls: "pred" },
      { key: "fut", points: futurePts, cls: "future" },
    ],
    band: { points: rows.filter((r) => r[`lo80_${m}`] != null).map((r) => [D(r), r[`lo80_${m}`], r[`hi80_${m}`]]) },
    yFmt: (v) => "$" + Math.round(v).toLocaleString("es-CL"),
    tip: (date) => {
      const iso = date.toISOString().slice(0, 10);
      const r = rows.find((x) => x.fecha_objetivo === iso);
      if (!r) return iso;
      const err = r.real != null ? ((r[`pred_${m}`] / r.real - 1) * 100) : null;
      return `<b>${fmtDate(iso)}</b><br>Real: ${clp(r.real, 2)}<br>Pronóstico: ${clp(r[`pred_${m}`], 2)}` +
        (err != null ? `<br>Error: ${err >= 0 ? "+" : ""}${pct(err, 2)}` : " (vigente)") +
        `<br><span style="color:var(--text-muted)">emitido el ${fmtDate(r.fecha_origen)} con dólar ${clp(r.spot, 2)}</span>`;
    },
  });
}

function renderMetrics() {
  const w = state.metrics.metricas[state.win];
  const tb = document.querySelector("#tbl-metrics tbody");
  tb.innerHTML = "";
  MODEL_ORDER.forEach((m) => {
    const r = w.modelos[m];
    const cls = (v, good) => (v == null ? "num" : good ? "num good" : "num");
    tb.appendChild(el("tr", { class: m === "ensemble" ? "main" : MAIN_MODELS.includes(m) ? "" : "bench" }, [
      el("td", { text: label(m) }),
      el("td", { class: r.theil_u < 1 && m !== "rw" ? "num good" : r.theil_u > 1 ? "num bad" : "num", text: num(r.theil_u, 3) }),
      el("td", { class: "num", text: num(r.rmse_clp, 1) }),
      el("td", { class: "num", text: num(r.mae_clp, 1) }),
      el("td", { class: "num", text: pct(r.mape_pct, 2) }),
      el("td", { class: cls(r.acierto_direccional_pct, r.acierto_direccional_pct > 50), text: r.acierto_direccional_pct == null ? "–" : pct(r.acierto_direccional_pct, 1) }),
      el("td", { class: cls(r.clark_west_p, r.clark_west_p < 0.05), text: r.clark_west_p == null ? "–" : num(r.clark_west_p, 3) }),
      el("td", { class: cls(r.pesaran_timmermann_p, r.pesaran_timmermann_p < 0.05), text: r.pesaran_timmermann_p == null ? "–" : num(r.pesaran_timmermann_p, 3) }),
      el("td", { class: "num", text: r.cobertura80_pct == null ? "–" : pct(r.cobertura80_pct, 0) }),
    ]));
  });
  document.getElementById("metrics-note").textContent =
    `Fechas objetivo entre ${fmtDate(w.desde)} y ${fmtDate(w.hasta)} (${w.modelos.rw.n.toLocaleString("es-CL")} pronósticos). ` +
    "Theil-U = RMSE del modelo / RMSE del paseo aleatorio. p Clark-West < 0,05: el modelo agrega información significativa " +
    "sobre el paseo aleatorio. p Pesaran-Timmermann < 0,05: acierta la dirección más que el azar. Tests con errores Newey-West (20 rezagos) " +
    "porque los pronósticos a 21 días se solapan. Los hiperparámetros se fijaron solo con datos anteriores a 2016.";
}

function renderRolling() {
  const D = (r) => new Date(r.fecha + "T00:00:00");
  const ms = MAIN_MODELS;
  const legend = el("div", { class: "chart-legend" });
  const series = ms.map((m, i) => {
    const color = cssVar(SERIES_VARS[i]);
    const it = el("span", { text: label(m) }); it.prepend(el("i", { style: `background:${color}` }));
    legend.appendChild(it);
    return { key: m, color, points: state.rolling.filter((r) => r[m] != null).map((r) => [D(r), r[m]]) };
  });
  const c = document.getElementById("chart-rolling");
  lineChart(c, {
    series, legend, refY: 1, yFmt: (v) => v.toFixed(2),
    tip: (date) => {
      const iso = date.toISOString().slice(0, 10);
      const r = state.rolling.find((x) => x.fecha === iso);
      return `<b>${fmtDate(iso)}</b>` + ms.map((m) => `<br>${label(m)}: ${r && r[m] != null ? r[m].toFixed(3) : "–"}`).join("");
    },
  });
}

function renderDrivers() {
  const box = document.getElementById("drivers");
  box.innerHTML = "";
  const d = state.metrics.drivers_hoy[state.drv] || {};
  const items = Object.entries(d).slice(0, 8);
  const max = Math.max(1e-9, ...items.map(([, v]) => Math.abs(v)));
  items.forEach(([k, v]) => {
    const w = (Math.abs(v) / max) * 50;
    const fill = el("div", { class: "drv-fill", style: `${v >= 0 ? "left:50%" : `left:${50 - w}%`};width:${w}%;background:var(${v >= 0 ? "--series-2" : "--series-3"})` });
    box.appendChild(el("div", { class: "drv-row" }, [
      el("div", { class: "lbl", text: featLabel(k), title: k }),
      el("div", { class: "drv-track" }, [fill]),
      el("div", { class: "v", text: (v >= 0 ? "+" : "") + (v * 100).toFixed(2) }),
    ]));
  });
  if (!items.length) box.textContent = "Sin datos.";
}

function renderBetas() {
  if (!state.betas.length) return;
  const keys = Object.keys(state.betas[0]).filter((k) => k !== "fecha");
  const D = (r) => new Date(r.fecha + "T00:00:00");
  const legend = el("div", { class: "chart-legend" });
  const series = keys.map((k, i) => {
    const color = cssVar(SERIES_VARS[i % SERIES_VARS.length]);
    const it = el("span", { text: featLabel(k) }); it.prepend(el("i", { style: `background:${color}` }));
    legend.appendChild(it);
    return { key: k, color, points: state.betas.map((r) => [D(r), r[k]]) };
  });
  const c = document.getElementById("chart-betas");
  lineChart(c, {
    series, legend, refY: 0, yFmt: (v) => (v * 100).toFixed(1),
    tip: (date) => {
      const iso = date.toISOString().slice(0, 10);
      const r = state.betas.find((x) => x.fecha === iso);
      return `<b>${fmtDate(iso)}</b>` + keys.map((k) => `<br>${featLabel(k)}: ${r ? (r[k] * 100).toFixed(2) : "–"}`).join("");
    },
  });
}

function renderLog() {
  const tb = document.querySelector("#tbl-log tbody");
  tb.innerHTML = "";
  const realBy = {};
  state.bt.forEach((r) => { if (r.real != null) realBy[r.fecha_objetivo] = r.real; });
  state.log.slice().reverse().slice(0, 60).forEach((r) => {
    const real = realBy[r.fecha_objetivo_estimada] ?? null;
    const err = real != null ? (r.pred_ensemble / real - 1) * 100 : null;
    tb.appendChild(el("tr", {}, [
      el("td", { text: fmtDate(r.fecha_origen) }),
      el("td", { text: fmtDate(r.fecha_objetivo_estimada) }),
      el("td", { class: "num", text: clp(r.spot, 2) }),
      el("td", { class: "num", text: clp(r.pred_ensemble, 2) }),
      el("td", { class: "num", text: real != null ? clp(real, 2) : "pendiente" }),
      el("td", { class: "num", text: err != null ? (err >= 0 ? "+" : "") + pct(err, 2) : "–" }),
    ]));
  });
}

function renderAll() {
  renderKPIs(); renderToday(); renderMain(); renderMetrics(); renderRolling(); renderDrivers(); renderBetas(); renderLog();
}

function wire() {
  const sel = document.getElementById("sel-model");
  MODEL_ORDER.forEach((m) => sel.appendChild(el("option", { value: m, text: label(m) })));
  sel.addEventListener("change", () => { state.model = sel.value; renderMain(); });
  document.querySelectorAll("#win-toggle button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#win-toggle button").forEach((x) => x.classList.toggle("active", x === b));
    state.win = b.dataset.win; renderMetrics();
  }));
  document.querySelectorAll("#drv-toggle button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#drv-toggle button").forEach((x) => x.classList.toggle("active", x === b));
    state.drv = b.dataset.m; renderDrivers();
  }));
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { renderMain(); renderRolling(); renderBetas(); }, 150); });
}

async function init() {
  const get = async (f, kind) => {
    const r = await fetch(BASE + f, { cache: "no-store" });
    if (!r.ok) throw new Error(`${f}: HTTP ${r.status}`);
    return kind === "json" ? r.json() : parseCSV(await r.text());
  };
  try {
    state.metrics = await get("metrics.json", "json");
    [state.bt, state.rolling, state.betas, state.log] = await Promise.all([
      get("backtest_predictions.csv"), get("rolling_theil.csv"), get("tvp_betas.csv"), get("forecast_log.csv"),
    ]);
  } catch (err) {
    document.getElementById("last-update").textContent =
      "Aún no hay datos del modelo: ejecuta `python -m fx.run_daily` (ver README).";
    console.error(err);
    return;
  }
  wire();
  renderAll();
}

init();
