"""Экспорт отчёта в Excel: лист «Сводка» + плоский лист «Данные».

Структура (переработана по фидбеку: удобная фильтрация и компактность):

* **Сводка** — итоги по категориям: количество SKU, продажи, средневзвешенный
  срок (SUMPRODUCT с перенормировкой весов) по каждому ритейлеру в двух
  вариантах весов (продажи / стоимость), доля SKU где Comfy ≥ конкурентов.
  Первая строка данных — «Вся выборка». Ячейки конкурентов, превышающие
  Comfy, подсвечиваются красным.
* **Данные** — все SKU одной непрерывной таблицей без промежуточных итогов:
  родной автофильтр Excel корректно фильтрует по категории и любым другим
  колонкам. Зебра-подсветка строк, заморозка шапки и колонок Код+Товар,
  условное форматирование «Откл.».

Для скорости на больших файлах (90k+ строк): workbook в режиме
``constant_memory`` (потоковая запись), значения пишутся числами без
формул, подготовка колонок векторизована.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from core import brand, metrics
from core.mapping import BrandOverride, ComfyMapping
from core.model import PAY_COMFY, pay_col

SHEET_SUMMARY = "Сводка"
SHEET_DATA = "Данные"


def default_filename(bank: str, generated: date | None = None) -> str:
    """`report_Monobank_2026-06-10.xlsx`; банк превращается в безопасный слаг."""
    generated = generated or date.today()
    slug = re.sub(r"[^\w\-]+", "_", bank, flags=re.UNICODE).strip("_") or "bank"
    return f"report_{slug}_{generated:%Y-%m-%d}.xlsx"


def _sheet_name(base: str, bank: str | None, used: set[str]) -> str:
    """Имя листа ≤31 символа, без запрещённых знаков, уникальное в книге."""
    name = base if bank is None else f"{base} — {bank}"
    name = re.sub(r"[\[\]:*?/\\]", " ", name).strip()
    if len(name) > 31:
        name = name[:31].rstrip()
    n, suffix = name, 2
    while n in used:
        tail = f" ({suffix})"
        n = name[: 31 - len(tail)] + tail
        suffix += 1
    used.add(n)
    return n


class _Formats:
    """Форматы в корпоративной палитре Comfy (см. ``core/brand.py``)."""

    def __init__(self, wb: xlsxwriter.Workbook):
        header_style = {
            "bold": True, "bg_color": brand.GREEN, "font_color": "#FFFFFF",
            "border": 1, "border_color": brand.GREEN_DARK,
            "text_wrap": True, "valign": "vcenter",
        }
        self.title = wb.add_format({"italic": True, "font_color": "#595959"})
        self.header = wb.add_format({**header_style, "align": "center"})
        self.header_left = wb.add_format(header_style)
        self.text = wb.add_format({})
        self.int = wb.add_format({"num_format": "0"})
        self.number = wb.add_format({"num_format": "General"})
        self.value = wb.add_format({"num_format": "#,##0"})
        self.structure = wb.add_format({"num_format": "0.00%"})
        self.percent = wb.add_format({"num_format": "0.0%"})
        self.term = wb.add_format({"num_format": "0.00"})
        self.share = wb.add_format({"num_format": "0%"})
        total_style = {"bold": True, "bg_color": brand.GREEN_TINT}
        self.bold_text = wb.add_format(total_style)
        self.bold_value = wb.add_format({**total_style, "num_format": "#,##0"})
        self.bold_int = wb.add_format({**total_style, "num_format": "0"})
        self.bold_term = wb.add_format({**total_style, "num_format": "0.00"})
        self.bold_share = wb.add_format({**total_style, "num_format": "0%"})
        self.cf_bad = wb.add_format({"bg_color": brand.RED_TINT,
                                     "font_color": "#9C0006"})
        self.cf_good = wb.add_format({"bg_color": brand.GREEN_TINT,
                                      "font_color": brand.GREEN_DARK})
        self.cf_band = wb.add_format({"bg_color": brand.GREEN_ZEBRA})


@dataclass
class _Col:
    header: str
    field: str
    numeric: bool
    fmt: str        # имя атрибута _Formats
    width: int


def _data_columns(has_sales: bool, competitor_names: dict[str, str]) -> list[_Col]:
    cols = [
        _Col("КодТовара", "sku", False, "int", 11),
        _Col("Товар", "name", False, "text", 42),
        _Col("Бренд", "brand", False, "text", 13),
        _Col("Бизнес", "business", False, "text", 16),
        _Col("Категория", "category", False, "text", 20),
        _Col("Подкатегория", "subcategory", False, "text", 22),
        _Col("Тип ассортимента", "assort_type", False, "text", 13),
        _Col("ТипАссортимента Общий", "assort_type_global", False, "text", 14),
        _Col("Цена Comfy", "price", True, "value", 10),
    ]
    if has_sales:
        cols += [
            _Col("Продажи", "sales", True, "value", 12),
            _Col("Структура (продажи)", "__structure_sales__", True, "structure", 10),
        ]
    cols.append(_Col("Структура (стоимость)", "__structure_price__", True, "structure", 10))
    cols.append(_Col("Платежей Comfy", PAY_COMFY, True, "int", 9))
    for domain, disp in competitor_names.items():
        cols.append(_Col(f"Платежей {disp}", pay_col(domain), True, "int", 9))
    cols += [
        _Col("Конкуренты MAX", "comp_max_bank", True, "int", 9),
        _Col("Откл. Comfy − конк.", "dev_bank", True, "int", 9),
        _Col("КМ Comfy (ТОП банки)", "beacon_comfy", True, "term", 9),
        _Col("КМ рынка (ТОП банки)", "beacon_market", True, "term", 9),
        _Col("Предлагаемый КМ", "beacon_proposed", True, "int", 10),
        _Col("Comfy MAX (файл)", "comfy_max", True, "int", 9),
        _Col("Скидка", "discount", True, "number", 8),
        _Col("Маржа кредитная", "margin", True, "percent", 9),
        _Col("Шаг", "step", True, "number", 6),
        _Col("Атрибут RMA", "attr_rma", False, "text", 10),
        _Col("Товар поставщика", "supplier_item", False, "text", 10),
        _Col("Остаток Comfy", "stock_comfy", False, "text", 9),
        _Col("Остаток SP", "stock_sp", False, "text", 9),
    ]
    return cols


def _cells(series: pd.Series, numeric: bool) -> list:
    """Колонка → список значений для записи: float | str | None (пусто)."""
    if numeric:
        arr = pd.to_numeric(series, errors="coerce").astype("float64")
        mask = arr.notna()
        values = arr.tolist()
        return [v if m else None for v, m in zip(values, mask.tolist())]
    out = series.astype(str).str.strip().tolist()
    return [None if v in ("", "nan", "None", "<NA>") else v for v in out]


def _title(bank: str, filters_desc: str, generated: date,
           mapping: ComfyMapping | None,
           brand_override: BrandOverride | None = None) -> str:
    parts = [f"Банк: {bank}", f"Сформировано: {generated:%Y-%m-%d}",
             f"Фильтры: {filters_desc}"]
    if mapping is not None and mapping.applies_to(bank):
        parts.append(
            f"Маппинг Comfy активен ({mapping.changed_count} знач.): "
            "платежи Comfy приведены к доступности этого банка"
        )
    if brand_override is not None and brand_override.is_active:
        parts.append(brand_override.describe())
    return "    ".join(parts)


# ---------------------------------------------------------------------------
# лист «Данные»
# ---------------------------------------------------------------------------

def _write_data_sheet(
    wb: xlsxwriter.Workbook,
    fmts: _Formats,
    sheet_name: str,
    wide: pd.DataFrame,
    has_sales: bool,
    competitor_names: dict[str, str],
    bank: str,
    filters_desc: str,
    generated: date,
    mapping: ComfyMapping | None,
    brand_override: BrandOverride | None,
) -> None:
    ws = wb.add_worksheet(sheet_name)
    columns = _data_columns(has_sales, competitor_names)
    ncols = len(columns)

    ws.write_string(0, 0, _title(bank, filters_desc, generated, mapping,
                                 brand_override), fmts.title)
    ws.set_row(1, 42)
    for c, col in enumerate(columns):
        ws.write_string(1, c, col.header, fmts.header)
        ws.set_column(c, c, col.width)
    ws.freeze_panes(2, 2)

    if not len(wide):
        ws.write_string(2, 0, "Нет данных: проверьте фильтры и выбранный банк.",
                        fmts.title)
        ws.autofilter(1, 0, 1, ncols - 1)
        return

    df = wide.sort_values(["category", "sku"]).reset_index(drop=True)
    if has_sales:
        df["__structure_sales__"] = metrics.structure(df, "sales")
    df["__structure_price__"] = metrics.structure(df, "price")

    # подготовка колонок единым махом — дальше только быстрые write_*
    prepared = []
    for col in columns:
        data = _cells(df[col.field], col.numeric)
        if col.field == "sku":   # числовые коды пишем числами (сортировка в Excel)
            data = [
                float(v) if isinstance(v, str) and v.isdigit() else v for v in data
            ]
        prepared.append((data, getattr(fmts, col.fmt)))

    nrows = len(df)
    write_number, write_string = ws.write_number, ws.write_string
    for r in range(nrows):
        er = r + 2
        for c, (data, fmt) in enumerate(prepared):
            v = data[r]
            if v is None:
                continue
            if isinstance(v, str):
                write_string(er, c, v, fmt)
            else:
                write_number(er, c, v, fmt)

    last = nrows + 1                      # 0-based номер последней строки данных
    ws.autofilter(1, 0, last, ncols - 1)  # весь диапазон → фильтр по категории работает

    full_range = f"A3:{xl_col_to_name(ncols - 1)}{last + 1}"
    ws.conditional_format(full_range, {
        "type": "formula", "criteria": "=MOD(ROW(),2)=0", "format": fmts.cf_band,
    })
    dev_idx = next(i for i, c in enumerate(columns) if c.field == "dev_bank")
    dl = xl_col_to_name(dev_idx)
    dev_range = f"{dl}3:{dl}{last + 1}"
    ws.conditional_format(dev_range, {
        "type": "cell", "criteria": "<", "value": 0, "format": fmts.cf_bad})
    ws.conditional_format(dev_range, {
        "type": "cell", "criteria": ">", "value": 0, "format": fmts.cf_good})


# ---------------------------------------------------------------------------
# лист «Сводка»
# ---------------------------------------------------------------------------

def _write_summary_sheet(
    wb: xlsxwriter.Workbook,
    fmts: _Formats,
    sheet_name: str,
    wide: pd.DataFrame,
    has_sales: bool,
    competitor_names: dict[str, str],
    bank: str,
    filters_desc: str,
    generated: date,
    mapping: ComfyMapping | None,
    brand_override: BrandOverride | None,
) -> None:
    ws = wb.add_worksheet(sheet_name)
    retailers = [(PAY_COMFY, "Comfy")] + [
        (pay_col(d), disp) for d, disp in competitor_names.items()
    ]
    pay_cols = [key for key, _ in retailers]
    weight_modes = (["sales"] if has_sales else []) + ["price"]
    mode_caption = {"sales": "продажи", "price": "стоимость"}

    headers: list[tuple[str, str, int]] = [("Категория", "header_left", 32),
                                           ("SKU, шт", "header", 8)]
    if has_sales:
        headers.append(("Продажи, грн", "header", 13))
    for mode in weight_modes:
        for _, disp in retailers:
            headers.append((f"{disp}\nвеса: {mode_caption[mode]}", "header", 10))
    headers.append(("Comfy ≥ конкурентов", "header", 11))

    ws.write_string(0, 0, _title(bank, filters_desc, generated, mapping,
                                 brand_override), fmts.title)
    ws.set_row(1, 44)
    for c, (text, fmt, width) in enumerate(headers):
        ws.write_string(1, c, text, getattr(fmts, fmt))
        ws.set_column(c, c, width)
    ws.freeze_panes(2, 1)

    if not len(wide):
        ws.write_string(2, 0, "Нет данных: проверьте фильтры и выбранный банк.",
                        fmts.title)
        return

    terms = {m: metrics.weighted_terms(wide, "sales" if m == "sales" else "price",
                                       pay_cols) for m in weight_modes}
    overall = {m: metrics.overall_terms(wide, "sales" if m == "sales" else "price",
                                        pay_cols) for m in weight_modes}
    win = metrics.win_share(wide)
    has_comp = wide["comp_max_bank"].notna() & wide["pay_comfy"].notna()
    win_overall = (
        float((wide.loc[has_comp, "dev_bank"] >= 0).mean()) if has_comp.any() else None
    )
    counts = wide.groupby("category").size()
    sales_sum = wide.groupby("category")["sales"].sum() if has_sales else None
    base_col = "sales" if has_sales else "price"
    order = (
        wide.groupby("category")[base_col].sum().sort_values(ascending=False).index
    )

    def write_row(er: int, label: str, count, sales_total, term_row: dict,
                  share, bold: bool) -> None:
        f_text = fmts.bold_text if bold else fmts.text
        f_int = fmts.bold_int if bold else fmts.int
        f_val = fmts.bold_value if bold else fmts.value
        f_term = fmts.bold_term if bold else fmts.term
        f_share = fmts.bold_share if bold else fmts.share
        c = 0
        ws.write_string(er, c, label, f_text); c += 1
        ws.write_number(er, c, int(count), f_int); c += 1
        if has_sales:
            ws.write_number(er, c, float(sales_total or 0), f_val); c += 1
        for mode in weight_modes:
            for key, _ in retailers:
                v = term_row[mode].get(key)
                if v is None or pd.isna(v):
                    pass
                else:
                    ws.write_number(er, c, float(v), f_term)
                c += 1
        if share is not None and not pd.isna(share):
            ws.write_number(er, c, float(share), f_share)

    write_row(
        2, "Вся выборка", len(wide),
        float(wide["sales"].sum()) if has_sales else None,
        {m: overall[m] for m in weight_modes}, win_overall, bold=True,
    )
    for i, cat in enumerate(order):
        term_row = {m: (terms[m].loc[cat] if cat in terms[m].index else {})
                    for m in weight_modes}
        write_row(
            3 + i, str(cat), counts.get(cat, 0),
            float(sales_sum.get(cat, 0)) if has_sales else None,
            term_row, win.get(cat), bold=False,
        )

    # конкурент дольше Comfy → красная ячейка (проигрываем по сроку)
    last_er = 3 + len(order)              # 1-based последняя строка данных
    first_block_col = 2 + (1 if has_sales else 0)
    for bi, _mode in enumerate(weight_modes):
        comfy_idx = first_block_col + bi * len(retailers)
        if len(retailers) < 2:
            continue
        comp_first = xl_col_to_name(comfy_idx + 1)
        comp_last = xl_col_to_name(comfy_idx + len(retailers) - 1)
        comfy_letter = xl_col_to_name(comfy_idx)
        rng = f"{comp_first}3:{comp_last}{last_er}"
        ws.conditional_format(rng, {
            "type": "formula",
            "criteria": (f"=AND(ISNUMBER({comp_first}3),"
                         f"ISNUMBER(${comfy_letter}3),"
                         f"{comp_first}3>${comfy_letter}3)"),
            "format": fmts.cf_bad,
        })


# ---------------------------------------------------------------------------
# публичный API
# ---------------------------------------------------------------------------

def export_report(
    path: str | Path,
    frames: list[tuple[str, pd.DataFrame]],
    competitor_names: dict[str, str],
    has_sales: bool,
    filters_desc: str = "без фильтров",
    generated: date | None = None,
    mapping: ComfyMapping | None = None,
    brand_override: BrandOverride | None = None,
) -> Path:
    """Пишет отчёт в ``path``.

    ``frames`` — список пар (банк, широкая отфильтрованная таблица; платежи
    Comfy уже приведены через маппинг и переопределение бренда, если они
    применимы — ``mapping``/``brand_override`` здесь только для пометки в
    шапке). Для одного банка листы называются «Сводка» и «Данные», для
    нескольких — с суффиксом банка.
    """
    if not frames:
        raise ValueError("Нечего экспортировать: не передано ни одного банка.")
    generated = generated or date.today()
    path = Path(path)

    wb = xlsxwriter.Workbook(
        str(path), {"constant_memory": True, "nan_inf_to_errors": True}
    )
    try:
        fmts = _Formats(wb)
        single = len(frames) == 1
        used: set[str] = set()
        for bank, wide in frames:
            args = (wide, has_sales, competitor_names, bank, filters_desc,
                    generated, mapping, brand_override)
            _write_summary_sheet(
                wb, fmts, _sheet_name(SHEET_SUMMARY, None if single else bank, used),
                *args,
            )
            _write_data_sheet(
                wb, fmts, _sheet_name(SHEET_DATA, None if single else bank, used),
                *args,
            )
    finally:
        wb.close()
    return path
