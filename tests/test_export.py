"""Тесты экспорта: «Сводка» + плоские «Данные», сверка итогов с эталоном."""

import openpyxl
import pandas as pd
import pytest

from core.mapping import BrandOverride, ComfyMapping
from core.model import ALL_BANKS, Dataset
from export.excel import SHEET_DATA, SHEET_SUMMARY, default_filename, export_report
from parsers.competitors import parse_competitors_csv
from parsers.sales import parse_sales
from test_metrics import load_reference_sales

ATTR_DEFAULTS = {
    "name": "", "brand": "", "subcategory": "", "business": "", "attr_rma": "",
    "supplier_item": "", "stock_comfy": "", "stock_sp": "", "discount": None,
    "margin": None, "step": None, "assort_type": "", "assort_type_global": "",
}
REF_NAMES = {
    "foxtrot.com.ua": "Foxtrot",
    "rozetka.com.ua": "Rozetka",
    "epicentrk.ua": "Epicentr",
}


def _reference_wide(example_xlsx_path) -> pd.DataFrame:
    """Широкая таблица из данных эталона + недостающие атрибуты-пустышки."""
    df, _ = load_reference_sales(example_xlsx_path)
    for col, value in ATTR_DEFAULTS.items():
        df[col] = value
    comp_cols = [f"pay::{d}" for d in REF_NAMES]
    df["comfy_max"] = df["pay_comfy"]
    df["comp_max_bank"] = df[comp_cols].max(axis=1)
    df["dev_bank"] = df["pay_comfy"] - df["comp_max_bank"]
    return df


@pytest.fixture
def dataset(kniga_path, sales_path) -> Dataset:
    return Dataset(parse_competitors_csv(kniga_path)).attach_sales(parse_sales(sales_path))


def _headers(ws, row=2) -> dict[str, int]:
    """заголовок → номер колонки (1-based) для строки заголовков."""
    return {str(c.value): c.column for c in ws[row] if c.value is not None}


def test_default_filename():
    from datetime import date
    name = default_filename("Monobank", date(2026, 6, 10))
    assert name == "report_Monobank_2026-06-10.xlsx"


def test_reference_totals_on_summary_sheet(tmp_path, example_xlsx_path):
    """Критерий приёмки: итоги «Сводки» совпадают с эталоном до 0.01."""
    wide = _reference_wide(example_xlsx_path)
    out = tmp_path / "ref.xlsx"
    export_report(out, [("Monobank", wide)], REF_NAMES, has_sales=True)

    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == [SHEET_SUMMARY, SHEET_DATA]
    ws = wb[SHEET_SUMMARY]
    heads = _headers(ws)

    # строка 3 — «Вся выборка», строка 4 — категория «Смартфон»
    assert ws.cell(row=3, column=1).value == "Вся выборка"
    assert ws.cell(row=4, column=1).value == "Смартфон"

    expectations_sales = {
        "Comfy": 16.41, "Foxtrot": 14.49, "Rozetka": 15.46, "Epicentr": 14.60,
    }
    expectations_price = {
        "Comfy": 16.28, "Foxtrot": 13.46, "Rozetka": 15.12, "Epicentr": 14.00,
    }
    for disp, expected in expectations_sales.items():
        col = heads[f"{disp}\nвеса: продажи"]
        assert ws.cell(row=4, column=col).value == pytest.approx(expected, abs=0.01)
    for disp, expected in expectations_price.items():
        col = heads[f"{disp}\nвеса: стоимость"]
        assert ws.cell(row=4, column=col).value == pytest.approx(expected, abs=0.01)

    # количество SKU и продажи категории
    assert ws.cell(row=4, column=heads["SKU, шт"]).value == 36
    assert ws.cell(row=4, column=heads["Продажи, грн"]).value == pytest.approx(72128373)

    # доля Comfy ≥ конкурентов в диапазоне [0; 1]
    share = ws.cell(row=3, column=heads["Comfy ≥ конкурентов"]).value
    assert 0 <= share <= 1


def test_data_sheet_is_flat_and_filterable(tmp_path, example_xlsx_path):
    """Плоская таблица: без промежуточных итогов, автофильтр на весь диапазон."""
    wide = _reference_wide(example_xlsx_path)
    out = tmp_path / "ref.xlsx"
    export_report(out, [("Monobank", wide)], REF_NAMES, has_sales=True)

    ws = openpyxl.load_workbook(out)[SHEET_DATA]
    heads = _headers(ws)
    assert "Monobank" in str(ws["A1"].value)

    n = len(wide)
    sku_values = [ws.cell(row=2 + i, column=heads["КодТовара"]).value
                  for i in range(1, n + 1)]
    assert all(v is not None for v in sku_values)         # непрерывный диапазон
    assert ws.cell(row=2 + n + 1, column=1).value is None  # и ничего после него

    last_col = ws.cell(row=2, column=len(heads)).column_letter
    assert ws.auto_filter.ref == f"A2:{last_col}{n + 2}"   # фильтр по категории работает
    assert ws.freeze_panes == "C3"
    assert len(list(ws.conditional_formatting)) >= 2       # зебра + откл.

    # структура первой строки данных = продажи / сумма по категории
    first_sales = ws.cell(row=3, column=heads["Продажи"]).value
    total = wide["sales"].sum()
    structure = ws.cell(row=3, column=heads["Структура (продажи)"]).value
    assert structure == pytest.approx(first_sales / total, rel=1e-6)

    # обе колонки структуры и платежи конкурентов на месте
    for h in ("Структура (стоимость)", "Платежей Comfy", "Платежей Foxtrot",
              "Откл. Comfy − конк.", "Comfy MAX (файл)"):
        assert h in heads, h


def test_export_from_fixtures_structure(tmp_path, dataset):
    bank = "Monobank"
    wide = dataset.wide(bank)
    out = tmp_path / "report.xlsx"
    export_report(out, [(bank, wide)], dataset.competitor_names(), has_sales=True)

    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == [SHEET_SUMMARY, SHEET_DATA]

    summary = wb[SHEET_SUMMARY]
    n_categories = wide["category"].nunique()
    labels = [summary.cell(row=r, column=1).value for r in range(3, 4 + n_categories)]
    assert labels[0] == "Вся выборка"
    assert len([v for v in labels if v]) == n_categories + 1

    data = wb[SHEET_DATA]
    heads = _headers(data)
    skus = {str(data.cell(row=2 + i, column=heads["КодТовара"]).value)
            for i in range(1, len(wide) + 1)}
    assert len(skus) == 12


def test_multibank_export_sheet_names(tmp_path, dataset):
    frames = [(b, dataset.wide(b)) for b in dataset.banks + [ALL_BANKS]]
    out = tmp_path / "multi.xlsx"
    export_report(out, frames, dataset.competitor_names(), has_sales=True)
    wb = openpyxl.load_workbook(out)
    assert len(wb.sheetnames) == 2 * len(frames)
    assert all(len(n) <= 31 for n in wb.sheetnames)
    assert any("Monobank" in n for n in wb.sheetnames)
    assert any("ПУМБ" in n for n in wb.sheetnames)


def test_export_without_sales_hides_sales_columns(tmp_path, dataset):
    wide = dataset.wide("ПУМБ")
    out = tmp_path / "no_sales.xlsx"
    export_report(out, [("ПУМБ", wide)], dataset.competitor_names(), has_sales=False)
    wb = openpyxl.load_workbook(out)
    data_heads = _headers(wb[SHEET_DATA])
    assert "Продажи" not in data_heads
    assert "Структура (продажи)" not in data_heads
    assert "Структура (стоимость)" in data_heads
    summary_heads = _headers(wb[SHEET_SUMMARY])
    assert "Продажи, грн" not in summary_heads
    assert not any("веса: продажи" in h for h in summary_heads)
    assert any("веса: стоимость" in h for h in summary_heads)


def test_mapping_reflected_in_export(tmp_path, kniga_path):
    dataset = Dataset(parse_competitors_csv(kniga_path))
    mapping = ComfyMapping(bank="Monobank", table={10: 5, 7: 4, 18: 9})
    wide = dataset.wide("Monobank", mapping)
    out = tmp_path / "mapped.xlsx"
    export_report(out, [("Monobank", wide)], dataset.competitor_names(),
                  has_sales=False, mapping=mapping)

    ws = openpyxl.load_workbook(out)[SHEET_DATA]
    assert "Маппинг Comfy активен" in str(ws["A1"].value)
    heads = _headers(ws)
    rows = {str(ws.cell(row=r, column=heads["КодТовара"]).value): r
            for r in range(3, 3 + 12)}
    r = rows["20671"]
    assert ws.cell(row=r, column=heads["Платежей Comfy"]).value == 5      # 10 → 5
    assert ws.cell(row=r, column=heads["Comfy MAX (файл)"]).value == 10   # исходное
    # Monobank foxtrot = 6 → откл. = 5 − 6 = −1
    assert ws.cell(row=r, column=heads["Откл. Comfy − конк."]).value == -1


def test_brand_override_reflected_in_export(tmp_path, kniga_path):
    ds = Dataset(parse_competitors_csv(kniga_path))
    override = BrandOverride(brand="Thomas", per_bank={"Monobank": 3})
    wide = ds.wide("Monobank", brand_override=override)
    out = tmp_path / "apple.xlsx"
    export_report(out, [("Monobank", wide)], ds.competitor_names(),
                  has_sales=False, brand_override=override)

    ws = openpyxl.load_workbook(out)[SHEET_DATA]
    assert "Thomas" in str(ws["A1"].value)     # пометка в шапке
    heads = _headers(ws)
    rows = {str(ws.cell(row=r, column=heads["КодТовара"]).value): r
            for r in range(3, 3 + 12)}
    r = rows["20671"]
    assert ws.cell(row=r, column=heads["Платежей Comfy"]).value == 3
    assert ws.cell(row=r, column=heads["Comfy MAX (файл)"]).value == 10


def test_export_empty_frame_writes_stub(tmp_path, dataset):
    wide = dataset.wide("Monobank").iloc[0:0]
    out = tmp_path / "empty.xlsx"
    export_report(out, [("Monobank", wide)], dataset.competitor_names(), has_sales=True)
    wb = openpyxl.load_workbook(out)
    assert "Нет данных" in str(wb[SHEET_DATA]["A3"].value)
    assert "Нет данных" in str(wb[SHEET_SUMMARY]["A3"].value)
