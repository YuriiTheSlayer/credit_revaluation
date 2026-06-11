"""Тесты модели: пивот банк→ритейлеры, режим «Все банки», продажи, фильтры."""

import pandas as pd
import pytest

from core.model import ALL_BANKS, Dataset, Filters
from parsers.competitors import parse_competitors_csv
from parsers.sales import parse_sales


@pytest.fixture
def dataset(kniga_path, sales_path) -> Dataset:
    return Dataset(parse_competitors_csv(kniga_path)).attach_sales(parse_sales(sales_path))


def _sku_row(wide: pd.DataFrame, sku: str) -> pd.Series:
    sub = wide[wide["sku"] == sku]
    assert len(sub) == 1
    return sub.iloc[0]


def test_wide_pivot_for_bank(dataset):
    wide = dataset.wide("Monobank")
    assert len(wide) == 12                      # одна строка на SKU
    row = _sku_row(wide, "20671")
    assert row["pay::foxtrot.com.ua"] == 6
    assert pd.isna(row["pay::rozetka.com.ua"])  # у 20671 нет данных rozetka
    assert row["pay_comfy"] == 10
    assert row["comp_max_bank"] == 6
    assert row["dev_bank"] == 4                 # Comfy 10 − лучший конкурент 6


def test_bank_switch_changes_numbers(dataset):
    mono = _sku_row(dataset.wide("Monobank"), "20671")
    privat = _sku_row(dataset.wide("ПриватБанк"), "20671")
    assert mono["pay::foxtrot.com.ua"] == 6
    assert privat["pay::foxtrot.com.ua"] == 15


def test_all_banks_aggregates_max(dataset):
    row = _sku_row(dataset.wide(ALL_BANKS), "20671")
    assert row["pay::foxtrot.com.ua"] == 15     # max(6, 15, 15)
    assert row["dev_bank"] == -5                # совпадает с «Откл.» из выгрузки


def test_sales_joined_left_missing_zero(dataset):
    wide = dataset.wide("Monobank")
    assert _sku_row(wide, "20671")["sales"] == pytest.approx(125000.50)
    assert _sku_row(wide, "523430")["sales"] == 0   # нет в файле продаж → 0


def test_extra_sales_sku_warned(dataset):
    assert any("999999" in w for w in dataset.sales_warnings)


def test_competitor_display_names(dataset):
    names = dataset.competitor_names()
    assert names == {"foxtrot.com.ua": "Foxtrot", "rozetka.com.ua": "Rozetka"}


def test_filters_multiselect_and_search(dataset):
    wide = dataset.wide("Monobank")
    by_brand = Filters(brand={"Thomas"}).apply(wide)
    assert set(by_brand["brand"]) == {"Thomas"}

    by_two = Filters(brand={"Thomas", "Koss"}).apply(wide)
    assert len(by_two) > len(by_brand)

    by_search = Filters(search="ROMA").apply(wide)
    assert len(by_search) >= 3
    assert all("ROMA" in n for n in by_search["name"])

    by_sku = Filters(search="20671").apply(wide)
    assert list(by_sku["sku"]) == ["20671"]

    # несколько значений через запятую — поиск по принципу «ИЛИ»
    by_list = Filters(search="20671, 925595").apply(wide)
    assert set(by_list["sku"]) == {"20671", "925595"}
    mixed = Filters(search="ROMA, INOX").apply(wide)
    assert set(mixed["sku"]) == {"20671", "502865", "508689", "910510", "913162"}
    assert len(Filters(search=" , ,").apply(wide)) == len(wide)  # только запятые


def test_filters_describe(dataset):
    assert Filters().describe() == "без фильтров"
    desc = Filters(brand={"Thomas"}, search="INOX").describe()
    assert "Thomas" in desc and "INOX" in desc


def test_complete_competitors_only_filter(dataset):
    """Галка «только SKU у всех конкурентов»: товары, представленные лишь у
    части конкурентов, исключаются из расчёта."""
    wide = dataset.wide("Monobank")
    flt = Filters(complete_competitors_only=True)
    sub = flt.apply(wide)
    assert len(sub) == 8                       # из 12 SKU полные данные у 8
    skus = set(sub["sku"])
    assert "20671" not in skus                 # только foxtrot → исключён
    assert "912618" not in skus and "922532" not in skus
    assert "20676" in skus and "925595" in skus
    # у оставшихся заполнены все колонки конкурентов
    comp_cols = [c for c in sub.columns if c.startswith("pay::")]
    assert sub[comp_cols].notna().all(axis=None)
    assert "у всех конкурентов" in flt.describe()
    assert flt.active_count == 1
