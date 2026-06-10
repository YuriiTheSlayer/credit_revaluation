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
  $("#appSubtitle").textContent = S.appSubtitle;

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
    graphite: "--graphite", muted: "--muted", bg: "--bg", surface: "--surface",
    outline: "--outline", orange: "--orange", orangeTint: "--orange-tint",
    red: "--red", redTint: "--red-tint",
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
    <input type="text" placeholder="Поиск значения…">
    <div class="ms-list"></div>
    <div class="ms-actions">
      <button class="btn text">Сбросить</button>
      <button class="btn primary">Готово</button>
    </div>`;
  wrap.appendChild(panel);

  const list = panel.querySelector(".ms-list");
  const drawList = (query = "") => {
    const q = query.trim().toLowerCase();
    list.innerHTML = "";
    for (const opt of options) {
      if (q && !opt.toLowerCase().includes(q)) continue;
      const item = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = msState[field].has(opt);
      cb.onchange = () => {
        cb.checked ? msState[field].add(opt) : msState[field].delete(opt);
        sync();
      };
      item.appendChild(cb);
      item.appendChild(document.createTextNode(opt));
      list.appendChild(item);
    }
  };
  panel.querySelector("input").oninput = (e) => drawList(e.target.value);
  const [resetBtn, doneBtn] = panel.querySelectorAll(".ms-actions .btn");
  resetBtn.onclick = () => { msState[field].clear(); drawList(); sync(); };
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
function renderCharts() {
  const card = $("#chartsCard");
  const show = S.loaded && S.charts;
  card.classList.toggle("hidden", !show);
  if (!show) return;
  renderTermsChart($("#chartTerms"), S.charts.terms);
  renderDevChart($("#chartDev"), S.charts.dev);
}

function chartScale(maxValue, height, padTop) {
  const step = Math.max(1, Math.ceil(maxValue / 5));
  const top = Math.max(step, Math.ceil(maxValue / step) * step);
  return { top, step, y: (v) => padTop + (height - padTop) * (1 - v / top) };
}

function renderTermsChart(host, terms) {
  const W = Math.max(640, host.clientWidth - 12), H = 300;
  const padL = 34, padB = 52, padT = 8;
  const plotH = H - padB;
  let maxV = 1;
  for (const s of terms.series)
    for (const v of s.values) if (v != null && v > maxV) maxV = v;
  const { top, step, y } = chartScale(maxV, plotH, padT);

  const n = terms.categories.length || 1;
  const groupW = (W - padL) / n;
  const barW = Math.min(20, Math.max(7, (groupW - 18) / terms.series.length));

  let svg = "";
  for (let g = 0; g <= top; g += step) {
    const gy = y(g);
    svg += `<line x1="${padL}" y1="${gy}" x2="${W}" y2="${gy}" stroke="var(--outline)" stroke-width="1"/>`;
    svg += `<text x="${padL - 6}" y="${gy + 3}" font-size="10" fill="var(--muted)" text-anchor="end">${g}</text>`;
  }
  terms.categories.forEach((cat, ci) => {
    const x0 = padL + ci * groupW + (groupW - barW * terms.series.length) / 2;
    terms.series.forEach((s, si) => {
      const v = s.values[ci];
      if (v == null) return;
      const by = y(v);
      svg += `<rect x="${x0 + si * barW + 1}" y="${by}" width="${barW - 2}" `
        + `height="${Math.max(1, plotH - by)}" rx="2" fill="${s.color}">`
        + `<title>${esc(cat)} — ${esc(s.name)}: ${v.toFixed(2)}</title></rect>`;
    });
    const label = cat.length > 17 ? cat.slice(0, 16) + "…" : cat;
    const lx = padL + ci * groupW + groupW / 2;
    svg += `<text x="${lx}" y="${plotH + 14}" font-size="10" fill="var(--muted)" `
      + `text-anchor="end" transform="rotate(-18 ${lx} ${plotH + 14})">${esc(label)}</text>`;
  });

  const legend = terms.series.map((s) =>
    `<span class="key"><span class="swatch" style="background:${s.color}"></span>${esc(s.name)}</span>`
  ).join("") + (terms.hint ? `<span class="hint">${esc(terms.hint)}</span>` : "");
  host.innerHTML = `<div class="legend">${legend}</div>`
    + `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" width="100%" height="${H}">${svg}</svg>`;
}

function renderDevChart(host, dev) {
  if (!dev.items.length) {
    host.innerHTML = '<p class="muted">Нет данных по конкурентам.</p>';
    return;
  }
  const W = Math.max(640, host.clientWidth - 12), H = 300;
  const padL = 38, padB = 30, padT = 8;
  const plotH = H - padB;
  const maxV = Math.max(...dev.items.map((d) => d.count), 1);
  const { top, step, y } = chartScale(maxV, plotH, padT);
  const n = dev.items.length;
  const slot = (W - padL) / n;
  const barW = Math.min(30, Math.max(8, slot - 8));
  const colors = { bad: "var(--red)", good: "var(--green)", zero: "var(--muted)" };

  let svg = "";
  for (let g = 0; g <= top; g += step) {
    const gy = y(g);
    svg += `<line x1="${padL}" y1="${gy}" x2="${W}" y2="${gy}" stroke="var(--outline)"/>`;
    svg += `<text x="${padL - 6}" y="${gy + 3}" font-size="10" fill="var(--muted)" text-anchor="end">${g}</text>`;
  }
  dev.items.forEach((item, i) => {
    const x = padL + i * slot + (slot - barW) / 2;
    const by = y(item.count);
    svg += `<rect x="${x}" y="${by}" width="${barW}" height="${Math.max(1, plotH - by)}" rx="2" `
      + `fill="${colors[item.kind]}"><title>Откл. ${esc(item.label)}: ${item.count} SKU</title></rect>`;
    svg += `<text x="${x + barW / 2}" y="${plotH + 14}" font-size="10" fill="var(--muted)" `
      + `text-anchor="middle">${esc(item.label)}</text>`;
  });
  host.innerHTML =
    `<p class="muted" style="font-size:11px;margin:0 0 6px">Откл. = платежи Comfy − лучший конкурент (по выбранному банку)</p>`
    + `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" width="100%" height="${H}">${svg}</svg>`;
}

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
