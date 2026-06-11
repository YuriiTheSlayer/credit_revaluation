/* Payment Terms Dashboard — фронтенд. Вся логика состояния в Python
   (webui/api.py): сюда приходит готовый снапшот, здесь — только рендер и
   обработчики. Без внешних библиотек: графики — рукописный SVG. */
"use strict";

const $ = (sel) => document.querySelector(sel);
let S = null;                 // текущий снапшот от Python
let msState = {};             // фильтры-мультиселекты: field -> Set
let openPanel = null;         // открытая панель мультиселекта

/* ------------------------------------------------------------- утилиты */
const fmtMoney = (v) => v == null ? "—" :
  Math.round(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
const fmtPct = (v) => v == null ? "—" : (v * 100).toFixed(2) + "%";
const fmtPay = (v) => v == null ? "—" : String(Math.round(v));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

let toastTimer = null;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), isError ? 6000 : 3500);
}

function busy(on) {
  $("#busy").classList.toggle("hidden", !on);
  $("#progress").classList.toggle("hidden", !on);
}

async function call(method, ...args) {
  busy(true);
  try {
    const res = await window.pywebview.api[method](...args);
    if (res && res.cancelled) return null;
    if (res) { S = res; render(); }
    if (res && res.error) toast(res.error, true);
    else if (res && res.saved) toast("Отчёт сохранён: " + res.saved);
    return res;
  } catch (err) {
    toast("Ошибка: " + err, true);
    return null;
  } finally {
    busy(false);
  }
}

/* ------------------------------------------------------------- рендер */
function render() {
  if (!S) return;
  applyTokens(S.brandTokens);
  $("#appTitle").textContent = S.appTitle;
  $("#appSubtitle").textContent =
    S.appSubtitle + (S.version ? " · v" + S.version : "");

  const stC = $("#statusCompetitors");
  if (S.statuses.competitors) { stC.textContent = "Конкуренты: " + S.statuses.competitors; stC.classList.add("ok"); }
  const stS = $("#statusSales");
  if (S.statuses.sales) { stS.textContent = "Продажи: " + S.statuses.sales; stS.classList.add("ok"); }

  $("#warningsBtn").classList.toggle("hidden", !S.warnings.length);
  $("#warningsBtn").title = "Предупреждения: " + S.warnings.length;

  $("#btnExport").disabled = !S.loaded;
  $("#btnMapping").disabled = !S.loaded;
  $("#btnApple").disabled = !S.loaded;
  $("#appleBtnLabel").textContent = "Доступность " + (S.apple.brand || "Apple");
  renderBadge("#mappingBadge", S.mapping.active, S.mapping.badge);
  renderBadge("#appleBadge", S.apple.active, S.apple.badge);

  $("#emptyHint").classList.toggle("hidden", S.loaded);
  $("#filtersBar").classList.toggle("hidden", !S.loaded);

  renderBanks();
  renderWeight();
  renderFilters();
  renderKpi();
  renderTable();
  renderCharts();
}

function applyTokens(tokens) {
  if (!tokens) return;
  const map = {
    green: "--green", greenDark: "--green-dark", greenTint: "--green-tint",
    graphite: "--graphite", darkGray: "--dark-gray", nearBlack: "--near-black",
    muted: "--muted", bg: "--bg", surface: "--surface",
    outline: "--outline", orange: "--orange", orangeTint: "--orange-tint",
    red: "--red", redTint: "--red-tint", font: "--font",
  };
  for (const [key, cssVar] of Object.entries(map)) {
    if (tokens[key]) document.documentElement.style.setProperty(cssVar, tokens[key]);
  }
}

function renderBadge(sel, active, text) {
  const el = $(sel);
  el.classList.toggle("hidden", !active);
  el.textContent = text || "";
}

function renderBanks() {
  const seg = $("#bankSeg");
  seg.classList.toggle("hidden", !S.loaded);
  if (!S.loaded) return;
  seg.innerHTML = "";
  for (const bank of [...S.banks, S.allBanks]) {
    const btn = document.createElement("button");
    btn.textContent = bank;
    btn.classList.toggle("active", bank === S.bank);
    btn.onclick = () => call("set_bank", bank);
    seg.appendChild(btn);
  }
}

function renderWeight() {
  const seg = $("#weightSeg");
  seg.classList.toggle("hidden", !S.loaded);
  if (!S.loaded) return;
  seg.innerHTML = "";
  const modes = [["sales", "Веса: продажи"], ["price", "Веса: стоимость"]];
  for (const [mode, label] of modes) {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.classList.toggle("active", mode === S.weight);
    btn.onclick = () => call("set_weight", mode);
    seg.appendChild(btn);
  }
}

/* ------------------------------------------------------------- фильтры */
function renderFilters() {
  if (!S.loaded) return;
  const host = $("#msHost");
  host.innerHTML = "";
  openPanel = null;
  msState = {};
  for (const field of Object.keys(S.filters.options)) {
    msState[field] = new Set(S.filters.selected[field] || []);
    host.appendChild(buildMultiselect(field));
  }
  const search = $("#search");
  if (document.activeElement !== search) search.value = S.filters.search || "";
  $("#completeOnly").checked = !!S.filters.completeOnly;
}

function buildMultiselect(field) {
  const label = S.filters.labels[field] || field;
  const options = S.filters.options[field] || [];
  const wrap = document.createElement("div");
  wrap.className = "ms";

  const btn = document.createElement("button");
  const sync = () => {
    const n = msState[field].size;
    const all = n === 0 || n === options.length;
    btn.textContent = all ? `${label}: все` : `${label}: ${n}`;
    btn.classList.toggle("on", !all);
  };
  sync();
  wrap.appendChild(btn);

  const panel = document.createElement("div");
  panel.className = "ms-panel hidden";
  panel.innerHTML = `
    <input type="text" placeholder="Поиск… (несколько — через запятую)">
    <div class="ms-count muted"></div>
    <div class="ms-list"></div>
    <div class="ms-actions">
      <button class="btn text">Выбрать все</button>
      <button class="btn text">Сбросить</button>
      <span class="spacer"></span>
      <button class="btn primary">Готово</button>
    </div>`;
  wrap.appendChild(panel);

  const list = panel.querySelector(".ms-list");
  const countEl = panel.querySelector(".ms-count");
  let currentQuery = "";

  // «пилосос, витяжка» → подходит всё, что содержит любой из терминов
  const matches = (opt, query) => {
    const terms = query.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
    if (!terms.length) return true;
    const low = opt.toLowerCase();
    return terms.some((t) => low.includes(t));
  };
  const visibleOptions = () => options.filter((o) => matches(o, currentQuery));
  const updateCount = () => {
    const visible = visibleOptions().length;
    countEl.textContent =
      `показано ${visible} из ${options.length} · выбрано ${msState[field].size}`;
  };

  const drawList = (query = "") => {
    currentQuery = query;
    list.innerHTML = "";
    for (const opt of visibleOptions()) {
      const item = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = msState[field].has(opt);
      cb.onchange = () => {
        cb.checked ? msState[field].add(opt) : msState[field].delete(opt);
        sync();
        updateCount();
      };
      item.appendChild(cb);
      item.appendChild(document.createTextNode(opt));
      list.appendChild(item);
    }
    updateCount();
  };
  panel.querySelector("input").oninput = (e) => drawList(e.target.value);

  const [selectAllBtn, resetBtn, doneBtn] =
    panel.querySelectorAll(".ms-actions .btn");
  selectAllBtn.onclick = () => {
    // выбираем только то, что прошло текущий поиск панели
    for (const opt of visibleOptions()) msState[field].add(opt);
    drawList(currentQuery);
    sync();
  };
  resetBtn.onclick = () => {
    msState[field].clear();
    drawList(currentQuery);
    sync();
  };
  doneBtn.onclick = () => { closePanels(); applyFilters(); };

  btn.onclick = (e) => {
    e.stopPropagation();
    const isOpen = !panel.classList.contains("hidden");
    closePanels();
    if (!isOpen) { drawList(); panel.classList.remove("hidden"); openPanel = panel; }
  };
  panel.onclick = (e) => e.stopPropagation();
  return wrap;
}

function closePanels() {
  if (openPanel) { openPanel.classList.add("hidden"); openPanel = null; }
}
document.addEventListener("click", closePanels);

function applyFilters() {
  const selected = {};
  for (const [field, set] of Object.entries(msState)) selected[field] = [...set];
  call("set_filters", {
    selected,
    search: $("#search").value,
    complete_only: $("#completeOnly").checked,
  });
}

/* ----------------------------------------------------------------- KPI */
function renderKpi() {
  const row = $("#kpiRow");
  row.innerHTML = "";
  if (!S.loaded) return;
  for (const card of S.kpi) {
    const el = document.createElement("div");
    el.className = "kpi" + (card.accent ? " accent" : "");
    el.innerHTML = `
      <div class="t">${esc(card.title)}</div>
      <div class="v ${card.color || ""}">${esc(card.value)}</div>
      <div class="s">${esc(card.sub)}</div>`;
    row.appendChild(el);
  }
}

/* -------------------------------------------------------------- таблица */
function renderTable() {
  const card = $("#tableCard");
  const t = S.table;
  const show = S.loaded && t && t.rows.length > 0;
  card.classList.toggle("hidden", !show);
  if (!show) return;

  $("#tableCaption").textContent = `Таблица SKU · ${t.totalLabel} строк`;
  const pager = $("#pager");
  pager.classList.toggle("hidden", t.pages <= 1);
  $("#pageInfo").textContent =
    `${t.page * 20 + 1}–${Math.min((t.page + 1) * 20, t.total)} из ${t.totalLabel} · стр. ${t.page + 1}/${t.pages}`;
  $("#pagePrev").onclick = () => t.page > 0 && call("set_table", t.page - 1);
  $("#pageNext").onclick = () => t.page < t.pages - 1 && call("set_table", t.page + 1);

  const numericTypes = new Set(["money", "pct", "pay", "dev"]);
  const thead = $("#skuTable thead");
  thead.innerHTML = "";
  const headRow = document.createElement("tr");
  for (const col of t.columns) {
    const th = document.createElement("th");
    const arrow = t.sort === col.key ? (t.asc ? " ▲" : " ▼") : "";
    th.textContent = col.label + arrow;
    if (numericTypes.has(col.type)) th.classList.add("num");
    th.onclick = () => call("set_table", null, col.key);
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);

  const tbody = $("#skuTable tbody");
  tbody.innerHTML = "";
  for (const row of t.rows) {
    const tr = document.createElement("tr");
    for (const col of t.columns) {
      const td = document.createElement("td");
      const v = row[col.key];
      switch (col.type) {
        case "money": td.textContent = fmtMoney(v); td.className = "num"; break;
        case "pct": td.textContent = fmtPct(v); td.className = "num"; break;
        case "pay": td.textContent = fmtPay(v); td.className = "num"; break;
        case "dev": {
          td.className = "num";
          if (v == null) td.innerHTML = '<span class="dev zero">—</span>';
          else {
            const kind = v < 0 ? "bad" : (v > 0 ? "good" : "zero");
            const sign = v > 0 ? "+" : "";
            td.innerHTML = `<span class="dev ${kind}">${sign}${Math.round(v)}</span>`;
          }
          break;
        }
        case "name":
          td.textContent = v ?? "";
          td.title = v ?? "";
          td.className = "name";
          break;
        default:
          td.textContent = v ?? "";
      }
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

/* -------------------------------------------------------------- графики */
let hiddenSeries = new Set();   // ритейлеры, выключенные кликом по легенде

function renderCharts() {
  const card = $("#chartsCard");
  const show = S.loaded && S.charts;
  card.classList.toggle("hidden", !show);
  if (!show) { hideTip(); return; }
  renderTermsChart($("#chartTerms"), S.charts.terms);
  renderDevChart($("#chartDev"), S.charts.dev);
}

function chartScale(maxValue, height, padTop) {
  const step = Math.max(1, Math.ceil(maxValue / 5));
  const top = Math.max(step, Math.ceil(maxValue / step) * step);
  return { top, step, y: (v) => padTop + (height - padTop) * (1 - v / top) };
}

/* --- фирменный тултип -------------------------------------------------- */
let tipEl = null;
function chartTip() {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "chart-tip hidden";
    document.body.appendChild(tipEl);
  }
  return tipEl;
}
function showTip(html, ev) {
  const el = chartTip();
  el.innerHTML = html;
  el.classList.remove("hidden");
  const pad = 14;
  const r = el.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - pad;
  el.style.left = x + "px";
  el.style.top = y + "px";
}
function hideTip() {
  if (tipEl) tipEl.classList.add("hidden");
}

function buildLegend(allSeries, hint) {
  const legend = document.createElement("div");
  legend.className = "legend";
  for (const s of allSeries) {
    const key = document.createElement("span");
    key.className = "key" + (hiddenSeries.has(s.name) ? " off" : "");
    key.title = "Клик — показать/скрыть серию";
    key.innerHTML = `<span class="swatch" style="background:${s.color}"></span>${esc(s.name)}`;
    key.onclick = () => {
      hiddenSeries.has(s.name) ? hiddenSeries.delete(s.name) : hiddenSeries.add(s.name);
      renderCharts();
    };
    legend.appendChild(key);
  }
  if (hint) {
    const h = document.createElement("span");
    h.className = "hint";
    h.textContent = hint;
    legend.appendChild(h);
  }
  return legend;
}

function toggleCategoryFilter(cat) {
  const cur = msState.category;
  if (!cur) return;
  if (cur.size === 1 && cur.has(cat)) cur.clear();   // повторный клик — снять
  else { cur.clear(); cur.add(cat); }
  hideTip();
  applyFilters();
}

function renderTermsChart(host, terms) {
  const series = terms.series.filter((s) => !hiddenSeries.has(s.name));
  host.innerHTML = "";
  host.appendChild(buildLegend(terms.series, terms.hint));
  if (!series.length) {
    host.insertAdjacentHTML("beforeend",
      '<p class="muted">Все серии скрыты — включите их кликом по легенде.</p>');
    return;
  }

  const W = Math.max(640, host.clientWidth - 12), H = 300;
  const padL = 34, padB = 52, padT = 8;
  const plotH = H - padB;
  let maxV = 1;
  for (const s of series)
    for (const v of s.values) if (v != null && v > maxV) maxV = v;
  const { top, step, y } = chartScale(maxV, plotH, padT);

  const n = terms.categories.length || 1;
  const groupW = (W - padL) / n;
  const barW = Math.min(20, Math.max(7, (groupW - 18) / series.length));

  let svg = "";
  // зоны наведения и подсветка группы — за барами
  terms.categories.forEach((_cat, ci) => {
    const gx = padL + ci * groupW;
    svg += `<rect class="band" data-ci="${ci}" x="${gx + 1}" y="${padT}" width="${groupW - 2}" `
      + `height="${plotH - padT}" rx="4" fill="rgba(42,189,19,.10)" opacity="0"/>`;
    svg += `<rect class="zone" data-ci="${ci}" x="${gx}" y="${padT}" width="${groupW}" `
      + `height="${plotH - padT}" fill="transparent"/>`;
  });
  for (let g = 0; g <= top; g += step) {
    const gy = y(g);
    svg += `<line x1="${padL}" y1="${gy}" x2="${W}" y2="${gy}" stroke="var(--outline)" stroke-width="1" pointer-events="none"/>`;
    svg += `<text x="${padL - 6}" y="${gy + 3}" font-size="10" fill="var(--muted)" text-anchor="end">${g}</text>`;
  }
  terms.categories.forEach((cat, ci) => {
    const x0 = padL + ci * groupW + (groupW - barW * series.length) / 2;
    series.forEach((s, si) => {
      const v = s.values[ci];
      if (v == null) return;
      const by = y(v);
      svg += `<rect class="bar" data-ci="${ci}" data-name="${esc(s.name)}" `
        + `x="${x0 + si * barW + 1}" y="${by}" width="${barW - 2}" `
        + `height="${Math.max(1, plotH - by)}" rx="2" fill="${s.color}"/>`;
    });
    const label = cat.length > 17 ? cat.slice(0, 16) + "…" : cat;
    const lx = padL + ci * groupW + groupW / 2;
    svg += `<text class="xlab" data-ci="${ci}" x="${lx}" y="${plotH + 14}" font-size="10" `
      + `fill="var(--muted)" text-anchor="end" transform="rotate(-18 ${lx} ${plotH + 14})">${esc(label)}</text>`;
  });

  host.insertAdjacentHTML("beforeend",
    `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" width="100%" height="${H}">${svg}</svg>`);

  const svgEl = host.querySelector("svg");
  const bars = [...svgEl.querySelectorAll(".bar")];
  const bands = [...svgEl.querySelectorAll(".band")];

  const tipHTML = (ci, hoverName) => {
    const cat = terms.categories[ci];
    const rows = series.map((s) => {
      const v = s.values[ci];
      return `<div class="row${s.name === hoverName ? " hl" : ""}">`
        + `<span class="sw" style="background:${s.color}"></span>`
        + `<span class="nm">${esc(s.name)}</span>`
        + `<span class="val">${v == null ? "—" : v.toFixed(2)}</span></div>`;
    }).join("");
    const comfy = terms.series.find((s) => s.name === "Comfy");
    const cv = comfy ? comfy.values[ci] : null;
    const best = Math.max(...terms.series
      .filter((s) => s.name !== "Comfy")
      .map((s) => s.values[ci])
      .filter((v) => v != null), -Infinity);
    let delta = "";
    if (cv != null && best > -Infinity) {
      const d = cv - best;
      const cls = d >= 0 ? "good" : "bad";
      delta = `<div class="row"><span class="nm">Comfy − лучший конкурент</span>`
        + `<span class="val ${cls}">${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(2)}</span></div>`;
    }
    return `<h4>${esc(cat)}</h4>${rows}${delta}`
      + `<div class="foot">Клик — фильтр по категории</div>`;
  };

  const highlight = (ci) => {
    bands.forEach((b) => b.setAttribute("opacity", b.dataset.ci === ci ? "1" : "0"));
    bars.forEach((b) => { b.style.opacity = (ci == null || b.dataset.ci === ci) ? "1" : ".35"; });
  };
  svgEl.addEventListener("mousemove", (ev) => {
    const ci = ev.target.dataset ? ev.target.dataset.ci : null;
    if (ci == null) { highlight(null); hideTip(); return; }
    highlight(ci);
    showTip(tipHTML(ci, ev.target.dataset.name || null), ev);
  });
  svgEl.addEventListener("mouseleave", () => { highlight(null); hideTip(); });
  svgEl.addEventListener("click", (ev) => {
    const ci = ev.target.dataset ? ev.target.dataset.ci : null;
    if (ci != null) toggleCategoryFilter(terms.categories[ci]);
  });
}

function renderDevChart(host, dev) {
  if (!dev.items.length) {
    host.innerHTML = '<p class="muted">Нет данных по конкурентам.</p>';
    return;
  }
  const W = Math.max(640, host.clientWidth - 12), H = 300;
  const padL = 38, padB = 30, padT = 8;
  const plotH = H - padB;
  const totalSku = dev.items.reduce((acc, d) => acc + d.count, 0);
  const maxV = Math.max(...dev.items.map((d) => d.count), 1);
  const { top, step, y } = chartScale(maxV, plotH, padT);
  const n = dev.items.length;
  const slot = (W - padL) / n;
  const barW = Math.min(30, Math.max(8, slot - 8));
  const colors = { bad: "var(--red)", good: "var(--green)", zero: "var(--muted)" };
  const kindLabel = { bad: "проигрываем", good: "выигрываем", zero: "паритет" };

  let svg = "";
  dev.items.forEach((_item, i) => {
    const gx = padL + i * slot;
    svg += `<rect class="zone" data-i="${i}" x="${gx}" y="${padT}" width="${slot}" `
      + `height="${plotH - padT}" fill="transparent"/>`;
  });
  for (let g = 0; g <= top; g += step) {
    const gy = y(g);
    svg += `<line x1="${padL}" y1="${gy}" x2="${W}" y2="${gy}" stroke="var(--outline)" pointer-events="none"/>`;
    svg += `<text x="${padL - 6}" y="${gy + 3}" font-size="10" fill="var(--muted)" text-anchor="end">${g}</text>`;
  }
  dev.items.forEach((item, i) => {
    const x = padL + i * slot + (slot - barW) / 2;
    const by = y(item.count);
    svg += `<rect class="bar" data-i="${i}" x="${x}" y="${by}" width="${barW}" `
      + `height="${Math.max(1, plotH - by)}" rx="2" fill="${colors[item.kind]}"/>`;
    svg += `<text x="${x + barW / 2}" y="${plotH + 14}" font-size="10" fill="var(--muted)" `
      + `text-anchor="middle">${esc(item.label)}</text>`;
  });
  host.innerHTML =
    `<p class="muted" style="font-size:11px;margin:0 0 6px">Откл. = платежи Comfy − лучший конкурент (по выбранному банку)</p>`
    + `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" width="100%" height="${H}">${svg}</svg>`;

  const svgEl = host.querySelector("svg");
  const bars = [...svgEl.querySelectorAll(".bar")];
  svgEl.addEventListener("mousemove", (ev) => {
    const i = ev.target.dataset ? ev.target.dataset.i : null;
    if (i == null) { bars.forEach((b) => b.style.opacity = "1"); hideTip(); return; }
    bars.forEach((b) => { b.style.opacity = b.dataset.i === i ? "1" : ".35"; });
    const item = dev.items[+i];
    const share = totalSku ? Math.round(item.count / totalSku * 100) : 0;
    showTip(
      `<h4>Откл. ${esc(item.label)} платежей</h4>`
      + `<div class="row"><span class="sw" style="background:${colors[item.kind]}"></span>`
      + `<span class="nm">${kindLabel[item.kind]}</span>`
      + `<span class="val">${item.count} SKU · ${share}%</span></div>`, ev);
  });
  svgEl.addEventListener("mouseleave", () => {
    bars.forEach((b) => b.style.opacity = "1");
    hideTip();
  });
}

/* перерисовка графиков под новую ширину окна */
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (S && S.charts) renderCharts(); }, 150);
});

/* -------------------------------------------------------------- модалки */
function modal({ title, bodyHTML, actions }) {
  const root = $("#modalRoot");
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-head"><h3>${esc(title)}</h3></div>
      <div class="modal-body">${bodyHTML}</div>
      <div class="modal-actions"></div>
    </div>`;
  const close = () => overlay.remove();
  overlay.onclick = (e) => { if (e.target === overlay) close(); };
  const actionsHost = overlay.querySelector(".modal-actions");
  for (const a of actions) {
    if (a.spacer) { const sp = document.createElement("span"); sp.className = "spacer"; actionsHost.appendChild(sp); continue; }
    const b = document.createElement("button");
    b.className = "btn " + (a.kind || "text");
    b.textContent = a.label;
    b.onclick = () => a.onClick(close, overlay);
    actionsHost.appendChild(b);
  }
  root.appendChild(overlay);
  return overlay;
}

function openWarnings() {
  modal({
    title: `Предупреждения (${S.warnings.length})`,
    bodyHTML: `<ul class="warn-list">${S.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`,
    actions: [{ label: "Закрыть", kind: "primary", onClick: (close) => close() }],
  });
}

function openMapping() {
  const m = S.mapping;
  const bankOptions = m.banks.map((b) =>
    `<option value="${esc(b)}" ${b === m.bank ? "selected" : ""}>${esc(b)}</option>`).join("");
  const rows = m.values.map((v) => `
    <div class="form-row">
      <span class="lbl">Макс. ${v.max} платежей</span><span class="arr">→</span>
      <input type="number" min="0" data-max="${v.max}" value="${v.mapped}">
    </div>`).join("");
  modal({
    title: "Маппинг доступности Comfy",
    bodyHTML: `
      <p class="hint">«Comfy. MAX платежей» в выгрузке — максимальная доступность
      (уровень ПриватБанк/ПУМБ). Укажите, какая доступность действует в выбранном
      банке: расчёты и экспорт в его разрезе используют приведённые значения.</p>
      <div class="form-row"><select id="mapBank">${bankOptions}</select></div>
      ${rows}`,
    actions: [
      { label: "Сбросить (1:1)", onClick: (_c, ov) => {
          ov.querySelectorAll("input[data-max]").forEach((i) => { i.value = i.dataset.max; });
        } },
      { spacer: true },
      { label: "Отмена", onClick: (close) => close() },
      { label: "Сохранить", kind: "primary", onClick: async (close, ov) => {
          const table = {};
          ov.querySelectorAll("input[data-max]").forEach((i) => { table[i.dataset.max] = i.value; });
          const res = await call("save_mapping", ov.querySelector("#mapBank").value, table);
          if (res && !res.error) close();
        } },
    ],
  });
}

function openApple() {
  const a = S.apple;
  const brandOptions = (a.brands.length ? a.brands : [a.brand]).map((b) =>
    `<option value="${esc(b)}" ${b === a.brand ? "selected" : ""}>${esc(b)}</option>`).join("");
  const rows = a.banks.map((b) => `
    <div class="form-row">
      <span class="lbl">${esc(b)}</span><span class="arr">→</span>
      <input type="number" min="0" data-bank="${esc(b)}" placeholder="из CSV"
             value="${a.perBank[b] ?? ""}">
    </div>`).join("");
  modal({
    title: "Доступность Comfy по бренду",
    bodyHTML: `
      <p class="hint">Кол-во платежей Comfy для товаров бренда в каждом банке —
      перезаписывает значение из CSV (приоритетнее маппинга Comfy). Пустое поле —
      оставить как в файле. В режиме «Все банки» берётся максимум по банкам.</p>
      <div class="form-row"><select id="appleBrand">${brandOptions}</select></div>
      ${rows}`,
    actions: [
      { label: "Сбросить", onClick: (_c, ov) => {
          ov.querySelectorAll("input[data-bank]").forEach((i) => { i.value = ""; });
        } },
      { spacer: true },
      { label: "Отмена", onClick: (close) => close() },
      { label: "Сохранить", kind: "primary", onClick: async (close, ov) => {
          const perBank = {};
          ov.querySelectorAll("input[data-bank]").forEach((i) => { perBank[i.dataset.bank] = i.value; });
          const res = await call("save_apple", ov.querySelector("#appleBrand").value, perBank);
          if (res && !res.error) close();
        } },
    ],
  });
}

/* ----------------------------------------------------------- drag&drop */
let dragDepth = 0;
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragDepth += 1;
  $("#dropOverlay").classList.remove("hidden");
});
document.addEventListener("dragleave", (e) => {
  e.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("#dropOverlay").classList.add("hidden");
});
document.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#dropOverlay").classList.add("hidden");
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!file) return;
  const path = file.pywebviewFullPath;     // полный путь добавляет pywebview ≥5
  if (!path) {
    toast("Перетаскивание не поддерживается этой версией WebView — используйте кнопки.", true);
    return;
  }
  call("load_dropped", path);
});

/* ----------------------------------------------------------- события UI */
function bindStatic() {
  $("#btnCompetitors").onclick = () => call("pick_competitors");
  $("#btnSales").onclick = () => call("pick_sales");
  $("#btnExport").onclick = () => call("export", $("#exportAll").checked);
  $("#btnMapping").onclick = openMapping;
  $("#btnApple").onclick = openApple;
  $("#warningsBtn").onclick = openWarnings;
  $("#btnResetFilters").onclick = () => call("reset_filters");
  $("#completeOnly").onchange = applyFilters;

  let searchTimer = null;
  $("#search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(applyFilters, 280);
  });

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      $("#chartTerms").classList.toggle("hidden", tab.dataset.tab !== "terms");
      $("#chartDev").classList.toggle("hidden", tab.dataset.tab !== "dev");
      if (S && S.charts) renderCharts();
    };
  });
}

window.addEventListener("pywebviewready", () => {
  bindStatic();
  call("boot");
});
