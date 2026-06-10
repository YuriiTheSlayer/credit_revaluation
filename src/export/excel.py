"""Экспорт отчёта в Excel по эталонному шаблону ``claude_example.xlsx``.

Два листа: «Ср. срок на продажи» (веса по продажам) и «Средний срок на
стоимость» (веса по цене). Данные группируются по категориям; под каждой
категорией — итоговая строка с живыми формулами Excel:

* ``Структура`` = ``=L3/L$38`` (доля строки в категории);
* итог по ритейлеру = ``=SUMPRODUCT(структура, платежи)``; если у ритейлера
  есть SKU без данных, используется вариант с перенормировкой весов
  ``=SUMPRODUCT(w, N(pay))/SUMPRODUCT(w, --ISNUMBER(pay))``;
* под итогами — подписи ритейлеров (Comfy / Foxtrot / …), как в эталоне.

В формулы также записывается кэшированное значение, посчитанное pandas, —
файл показывает корректные цифры даже до пересчёта Excel.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from core import metrics
from core.model import PAY_COMFY, pay_col

SHEET_SALES = "Ср. срок на продажи"
SHEET_PRICE = "Средний срок на стоимость"

#: (заголовок, внутренняя колонка, тип ячейки)
_BASE_LEFT = [
    ("КодТовара", "sku", "sku"),
    ("Товар", "name", "text"),
    ("Бренд", "brand", "text"),
    ("Подкатегория", "subcategory", "text"),
    ("Категория", "category", "text"),
    ("Бизнес", "business", "text"),
    ("Атрибут_RMA", "attr_rma", "text"),
    ("Товар поставщика", "supplier_item", "text"),
    ("Остаток_Comfy", "stock_comfy", "text"),
    ("Остаток_SP", "stock_sp", "text"),
    ("Скидка", "discount", "number"),
]
_BASE_RIGHT = [
    ("Конкуренты. MAX платежей", "comp_max_bank", "int"),
    ("Comfy. MAX платежей", PAY_COMFY, "int"),       # итог Comfy — под этой колонкой
    ("Откл. MAX платежей", "dev_bank", "dev"),
    ("Шаг", "step", "number"),
    ("Маржа кредитная", "margin", "percent"),
    ("Тип ассортимента", "assort_type", "text"),
    ("ТипАссортиментаОбщий", "assort_type_global", "text"),
    ("Кол-во Платежей Comfy", PAY_COMFY, "int"),     # дубль без итога — как в эталоне
]

_COL_WIDTHS = {
    "sku": 11, "name": 46, "text": 16, "number": 10, "int": 11,
    "dev": 11, "percent": 10, "value": 13, "structure": 11, "pay": 13,
}


def default_filename(bank: str, generated: date | None = None) -> str:
    """`report_Monobank_2026-06-10.xlsx`; банк транслитерируется в безопасный слаг."""
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
    def __init__(self, wb: xlsxwriter.Workbook):
        self.title = wb.add_format({"italic": True, "font_color": "#595959"})
        self.header = wb.add_format({
            "bold": True, "bg_color": "#DDEBF7", "border": 1,
            "text_wrap": True, "valign": "top",
        })
        self.text = wb.add_format({})
        self.sku = wb.add_format({"num_format": "0"})
        self.number = wb.add_format({"num_format": "General"})
        self.int = wb.add_format({"num_format": "0"})
        self.value = wb.add_format({"num_format": "#,##0"})
        self.structure = wb.add_format({"num_format": "0.00%"})
        self.percent = wb.add_format({"num_format": "0.0%"})
        total = {"bold": True, "top": 1}
        self.total_value = wb.add_format({**total, "num_format": "#,##0"})
        self.total_structure = wb.add_format({**total, "num_format": "0%"})
        self.total_term = wb.add_format({**total, "num_format": "0.00"})
        self.total_blank = wb.add_format(total)
        self.label = wb.add_format({"bold": True, "align": "center"})
        self.cf_bad = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})
        self.cf_good = wb.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"})


def _columns(weight_mode: str, competitor_names: dict[str, str]):
    """Список колонок листа: (заголовок, внутреннее имя, тип)."""
    cols = list(_BASE_LEFT)
    if weight_mode == "sales":
        cols += [
            ("Продажи", "sales", "value"),
            ("Структура", "__structure__", "structure"),
            ("Цена Comfy *", "price", "value"),
        ]
    else:
        cols += [
            ("Цена Comfy *", "price", "value"),
            ("Структура", "__structure__", "structure"),
        ]
    cols += _BASE_RIGHT
    for domain, display in competitor_names.items():
        cols.append((f"Кол-во Платежей {display}", pay_col(domain), "pay"))
    return cols


def _write_sheet(
    wb: xlsxwriter.Workbook,
    fmts: _Formats,
    sheet_name: str,
    wide: pd.DataFrame,
    weight_mode: str,            # 'sales' | 'price'
    competitor_names: dict[str, str],
    bank: str,
    filters_desc: str,
    generated: date,
) -> None:
    ws = wb.add_worksheet(sheet_name)
    columns = _columns(weight_mode, competitor_names)
    ncols = len(columns)
    value_col = "sales" if weight_mode == "sales" else "price"

    cell_fmt = {
        "sku": fmts.sku, "text": fmts.text, "number": fmts.number, "int": fmts.int,
        "dev": fmts.int, "value": fmts.value, "structure": fmts.structure,
        "percent": fmts.percent, "pay": fmts.int,
    }

    title = (
        f"Банк: {bank}    Сформировано: {generated:%Y-%m-%d}    "
        f"Фильтры: {filters_desc}"
    )
    if ncols > 1:
        ws.merge_range(0, 0, 0, ncols - 1, title, fmts.title)
    else:
        ws.write(0, 0, title, fmts.title)

    for c, (header, _, kind) in enumerate(columns):
        ws.write(1, c, header, fmts.header)
        ws.set_column(c, c, _COL_WIDTHS.get(kind, 12))
    ws.set_row(1, 30)
    ws.freeze_panes(2, 2)
    ws.autofilter(1, 0, 1, ncols - 1)  # автофильтр по строке заголовка, как в эталоне

    if not len(wide):
        ws.write(2, 0, "Нет данных: проверьте фильтры и выбранный банк.", fmts.title)
        return

    df = wide.sort_values(["category", "sku"]).reset_index(drop=True)
    weights = metrics.structure(df, value_col)
    pay_columns = [PAY_COMFY] + [pay_col(d) for d in competitor_names]
    terms = metrics.weighted_terms(df, value_col, pay_columns)

    letter = {name: xl_col_to_name(i) for i, (_, name, _k) in enumerate(columns)}
    structure_idx = next(i for i, c in enumerate(columns) if c[1] == "__structure__")
    value_idx = next(i for i, c in enumerate(columns) if c[1] == value_col)
    dev_idx = next(i for i, c in enumerate(columns) if c[1] == "dev_bank")
    comfy_total_idx = next(
        i for i, c in enumerate(columns) if c[0] == "Comfy. MAX платежей"
    )
    vl = letter[value_col]
    sl = xl_col_to_name(structure_idx)

    categories = list(dict.fromkeys(df["category"]))
    multi_cat = len(categories) > 1
    row = 2  # 0-based; первая строка данных = 3 в нотации Excel

    for cat in categories:
        block = df[df["category"] == cat]
        block_w = weights.loc[block.index]
        r0, r1 = row, row + len(block) - 1            # 0-based границы блока
        e0, e1, etot = r0 + 1, r1 + 1, r1 + 2          # 1-based для формул Excel

        for offset, (_, rec) in enumerate(block.iterrows()):
            r = r0 + offset
            for c, (_, name, kind) in enumerate(columns):
                if name == "__structure__":
                    ws.write_formula(
                        r, c, f"={vl}{r + 1}/{vl}${etot}",
                        fmts.structure, float(block_w.loc[rec.name]),
                    )
                    continue
                val = rec[name]
                if pd.isna(val) or val == "":
                    ws.write_blank(r, c, None, cell_fmt[kind])
                elif kind == "sku":
                    sku = str(val)
                    if sku.isdigit():
                        ws.write_number(r, c, int(sku), fmts.sku)
                    else:
                        ws.write_string(r, c, sku, fmts.text)
                elif kind in ("number", "int", "dev", "value", "percent", "pay"):
                    ws.write_number(r, c, float(val), cell_fmt[kind])
                else:
                    ws.write_string(r, c, str(val), fmts.text)
            row += 1

        # --- итоговая строка категории -------------------------------------
        tr = row
        for c in range(ncols):
            ws.write_blank(tr, c, None, fmts.total_blank)
        ws.write_formula(
            tr, value_idx, f"=SUM({vl}{e0}:{vl}{e1})",
            fmts.total_value, float(block[value_col].fillna(0).sum()),
        )
        ws.write_formula(
            tr, structure_idx, f"=SUM({sl}{e0}:{sl}{e1})",
            fmts.total_structure, float(block_w.sum()),
        )

        def _term_formula(col_letter: str) -> str:
            rng = f"{col_letter}{e0}:{col_letter}{e1}"
            wrng = f"{sl}${e0}:{sl}${e1}"
            return (
                f"=SUMPRODUCT({wrng},N({rng}))/SUMPRODUCT({wrng},--ISNUMBER({rng}))"
            )

        def _term_formula_simple(col_letter: str) -> str:
            rng = f"{col_letter}{e0}:{col_letter}{e1}"
            return f"=SUMPRODUCT({sl}${e0}:{sl}${e1},{rng})"

        label_targets: list[tuple[int, str]] = []
        term_cells = [(comfy_total_idx, PAY_COMFY, "Comfy")] + [
            (next(i for i, cc in enumerate(columns) if cc[1] == pay_col(d)),
             pay_col(d), disp)
            for d, disp in competitor_names.items()
        ]
        for col_idx, pay_name, display in term_cells:
            value = terms.loc[cat, pay_name] if cat in terms.index else float("nan")
            cl = xl_col_to_name(col_idx)
            has_gaps = bool(block[pay_name].isna().any())
            if pd.isna(value):
                ws.write_blank(tr, col_idx, None, fmts.total_blank)
            else:
                formula = _term_formula(cl) if has_gaps else _term_formula_simple(cl)
                ws.write_formula(tr, col_idx, formula, fmts.total_term, float(value))
            label_targets.append((col_idx, display))

        for col_idx, display in label_targets:
            ws.write_string(tr + 1, col_idx, display, fmts.label)
        if multi_cat:
            ws.write_string(tr + 1, 0, cat, fmts.label)

        # условное форматирование «Откл. MAX платежей» по строкам блока
        dl = xl_col_to_name(dev_idx)
        dev_range = f"{dl}{e0}:{dl}{e1}"
        ws.conditional_format(dev_range, {
            "type": "cell", "criteria": "<", "value": 0, "format": fmts.cf_bad,
        })
        ws.conditional_format(dev_range, {
            "type": "cell", "criteria": ">", "value": 0, "format": fmts.cf_good,
        })

        row = tr + 2
        if multi_cat:
            row += 1  # пустая строка между категориями


def export_report(
    path: str | Path,
    frames: list[tuple[str, pd.DataFrame]],
    competitor_names: dict[str, str],
    has_sales: bool,
    filters_desc: str = "без фильтров",
    generated: date | None = None,
) -> Path:
    """Пишет отчёт в ``path``.

    ``frames`` — список пар (банк, широкая отфильтрованная таблица). Для одного
    банка имена листов в точности как в эталоне; для нескольких — по паре
    листов на банк с суффиксом банка.
    """
    if not frames:
        raise ValueError("Нечего экспортировать: не передано ни одного банка.")
    generated = generated or date.today()
    path = Path(path)

    wb = xlsxwriter.Workbook(str(path), {"nan_inf_to_errors": True})
    try:
        fmts = _Formats(wb)
        single = len(frames) == 1
        used: set[str] = set()
        for bank, wide in frames:
            sheets = (
                [(SHEET_SALES, "sales"), (SHEET_PRICE, "price")]
                if single
                else [("На продажи", "sales"), ("На стоимость", "price")]
            )
            for base, mode in sheets:
                if mode == "sales" and not has_sales:
                    # без файла продаж лист «на продажи» не имеет смысла
                    continue
                name = _sheet_name(base, None if single else bank, used)
                _write_sheet(
                    wb, fmts, name, wide, mode, competitor_names,
                    bank=bank, filters_desc=filters_desc, generated=generated,
                )
    finally:
        wb.close()
    return path
