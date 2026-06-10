"""Тесты парсера файла продаж: csv/xlsx, автоопределение колонок, дубликаты."""

import io

import openpyxl
import pytest

from parsers.sales import SalesParseError, parse_sales


def test_parses_cp1251_csv_with_european_numbers(sales_path):
    data = parse_sales(sales_path)
    df = data.df.set_index("sku")
    assert df.loc["20671", "sales"] == pytest.approx(125000.50)
    assert df.loc["20676", "sales"] == pytest.approx(98500)
    assert df.loc["502865", "sales"] == 0
    assert len(df) == 11


def test_duplicates_are_summed():
    raw = "КодТовара,Оборот\n20671,100\n20671,50\n20676,7\n".encode("utf-8")
    data = parse_sales(raw, source_name="dup.csv")
    df = data.df.set_index("sku")
    assert df.loc["20671", "sales"] == 150
    assert any("Дубликаты" in w for w in data.warnings)


def test_headerless_two_columns_detected():
    raw = "20671,500.5\n20676,300\n".encode("utf-8")
    data = parse_sales(raw)
    df = data.df.set_index("sku")
    assert df.loc["20671", "sales"] == pytest.approx(500.5)


def test_header_synonyms_detected():
    raw = "SKU;Продажи\n20671;1000\n".encode("utf-8")
    data = parse_sales(raw)
    assert data.df.iloc[0]["sku"] == "20671"
    assert data.df.iloc[0]["sales"] == 1000


def test_xlsx_first_sheet():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["КодТовара", "Оборот"])
    ws.append([20671, 12345.5])
    ws.append([20676, 700])
    buf = io.BytesIO()
    wb.save(buf)
    data = parse_sales(buf.getvalue(), source_name="sales.xlsx")
    df = data.df.set_index("sku")
    assert df.loc["20671", "sales"] == pytest.approx(12345.5)
    assert df.loc["20676", "sales"] == 700


def test_empty_file_rejected():
    with pytest.raises(SalesParseError):
        parse_sales(b"")


def test_no_numeric_column_rejected():
    raw = "КодТовара,Комментарий\n20671,привет\n20676,мир\n".encode("utf-8")
    with pytest.raises(SalesParseError):
        parse_sales(raw)
