"""Тесты парсера CSV конкурентов: кодировка, развёртка строк, числа с запятой."""

import pandas as pd
import pytest

from parsers.competitors import (
    CompetitorsParseError,
    competitor_display_name,
    normalize_header,
    normalize_product_name,
    parse_competitors_csv,
    parse_fraction,
    parse_number,
)


def _row(data, sku, bank, competitor):
    long = data.long
    sub = long[(long["sku"] == sku) & (long["bank"] == bank) & (long["competitor"] == competitor)]
    assert len(sub) == 1, f"ожидали ровно одну строку {sku}/{bank}/{competitor}"
    return sub.iloc[0]


# ---------------------------------------------------------------------------
# низкоуровневые помощники
# ---------------------------------------------------------------------------

def test_parse_number_european_formats():
    assert parse_number("6\xa0649") == 6649
    assert parse_number("125 000,50") == 125000.50
    assert parse_number("14,3%") == 14.3
    assert parse_number("10,91") == 10.91
    assert parse_number("-5") == -5
    assert parse_number("-") is None
    assert parse_number("") is None
    assert parse_number(None) is None


def test_parse_fraction():
    assert parse_fraction("14,3%") == pytest.approx(0.143)
    assert parse_fraction("-2,9%") == pytest.approx(-0.029)
    assert parse_fraction("-") is None


def test_normalize_header_collapses_spaces():
    assert normalize_header("Comfy.   MAX платежей") == "Comfy. MAX платежей"
    assert normalize_header("max(КолвоПлатежей);") == "max(КолвоПлатежей)"


def test_normalize_product_name():
    assert normalize_product_name("INOX 1530;20671", "20671") == "INOX 1530"
    assert normalize_product_name("INOX 1530", "20671") == "INOX 1530"


def test_competitor_display_name():
    assert competitor_display_name("foxtrot.com.ua") == "Foxtrot"
    assert competitor_display_name("rozetka.com.ua") == "Rozetka"


# ---------------------------------------------------------------------------
# разбор образца Книга31.csv (cp1251)
# ---------------------------------------------------------------------------

def test_parses_sample_without_manual_reencoding(kniga_path):
    data = parse_competitors_csv(kniga_path)
    assert data.encoding == "cp1251"
    assert len(data.long) == 59            # 61 строка − заголовок − служебная
    assert data.sku_count == 12
    # списки банков и конкурентов — distinct из файла, в порядке появления
    assert data.banks == ["Monobank", "ПриватБанк", "ПУМБ"]
    assert data.competitors == ["foxtrot.com.ua", "rozetka.com.ua"]


def test_unfolds_quoted_block_and_numbers(kniga_path):
    data = parse_competitors_csv(kniga_path)
    row = _row(data, "20671", "Monobank", "foxtrot.com.ua")
    assert row["name"] == "INOX 1530"      # хвост «;КодТовара» отрезан
    assert row["brand"] == "Thomas"
    assert row["price"] == 6649            # «6 649» с NBSP-разделителем тысяч
    assert row["margin"] == pytest.approx(0.143)
    assert row["competitors_max"] == 15
    assert row["comfy_max"] == 10
    assert row["deviation_max"] == -5
    assert row["step"] == 2
    assert row["payments"] == 6
    assert row["bank"] == "Monobank"


def test_step_placeholder_becomes_null(kniga_path):
    data = parse_competitors_csv(kniga_path)
    row = _row(data, "912618", "Monobank", "foxtrot.com.ua")
    assert pd.isna(row["step"])            # «-» → NULL


def test_same_sku_repeats_per_bank_competitor(kniga_path):
    data = parse_competitors_csv(kniga_path)
    sub = data.long[data.long["sku"] == "20676"]
    combos = set(zip(sub["bank"], sub["competitor"]))
    assert len(sub) == 6 and len(combos) == 6   # 3 банка × 2 конкурента


def test_utf8_variant_parses_identically(kniga_path):
    text = kniga_path.read_bytes().decode("cp1251")
    data = parse_competitors_csv(text.encode("utf-8"), source_name="utf8.csv")
    assert data.encoding == "utf-8"
    assert len(data.long) == 59
    assert data.banks == ["Monobank", "ПриватБанк", "ПУМБ"]


def test_duplicate_rows_take_max_payments(kniga_path):
    text = kniga_path.read_bytes().decode("cp1251")
    lines = text.splitlines()
    dup = lines[2].replace(",6\"", ",9\"")      # та же SKU/банк/конкурент, платежей 9
    text_dup = "\n".join(lines + [dup])
    data = parse_competitors_csv(text_dup.encode("utf-8"))
    row = _row(data, "20671", "Monobank", "foxtrot.com.ua")
    assert row["payments"] == 9
    assert any("Дубликаты" in w for w in data.warnings)


def test_missing_required_columns_is_explained():
    csv_text = "КодТовара,Товар,Категория\n1,Товар А,Кат\n"
    with pytest.raises(CompetitorsParseError) as err:
        parse_competitors_csv(csv_text.encode("utf-8"))
    assert "Банк" in str(err.value)


def test_binary_file_rejected():
    with pytest.raises(CompetitorsParseError):
        parse_competitors_csv(b"\x00\x01\x02\x03 binary junk \x00\x00")


def test_xlsx_passed_as_csv_rejected(example_xlsx_path):
    with pytest.raises(CompetitorsParseError) as err:
        parse_competitors_csv(example_xlsx_path.read_bytes())
    assert "Excel" in str(err.value)


def test_empty_file_rejected():
    with pytest.raises(CompetitorsParseError):
        parse_competitors_csv(b"")
