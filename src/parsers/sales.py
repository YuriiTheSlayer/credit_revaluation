"""Парсер файла продаж: две колонки — КодТовара и Оборот (README §2.2).

Поддерживаются `.csv` (cp1251/UTF-8, разделитель `,`/`;`/таб — автоопределение)
и `.xlsx` (первый лист). Названия колонок не критичны: колонка с кодами и
числовая колонка определяются по заголовкам-синонимам, а если их нет — по
содержимому. Дубликаты кодов суммируются.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from parsers.competitors import decode_bytes, parse_number, to_number_series, _canon_sku

log = logging.getLogger(__name__)

_SKU_HEADERS = {"кодтовара", "код товара", "sku", "код", "артикул", "товар"}
_SALES_HEADERS = {"оборот", "продажи", "сумма", "выручка", "sales", "turnover", "amount"}
_XLSX_MAGIC = b"PK\x03\x04"


class SalesParseError(ValueError):
    """Файл продаж не может быть разобран; message — понятное объяснение."""


@dataclass
class SalesData:
    """Продажи по SKU: df с колонками `sku` (str) и `sales` (float)."""

    df: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    source_name: str = ""

    @property
    def total(self) -> float:
        return float(self.df["sales"].sum()) if len(self.df) else 0.0


_DETECT_SAMPLE = 1000   # для определения колонок достаточно выборки


def _looks_like_sku(values: pd.Series) -> float:
    """Доля значений, похожих на код товара (целое число из 3+ цифр)."""
    s = values.head(_DETECT_SAMPLE).dropna().astype(str).str.strip()
    s = s[s != ""]
    if s.empty:
        return 0.0
    return float(s.str.fullmatch(r"\d{3,}(\.0)?").mean())


def _numeric_share(values: pd.Series) -> float:
    """Доля значений, разбираемых как число (с учётом «европейского» формата)."""
    s = values.head(_DETECT_SAMPLE).dropna()
    if s.empty:
        return 0.0
    return float(to_number_series(s).notna().mean())


def _detect_columns(df: pd.DataFrame) -> tuple[object, object]:
    """Возвращает (колонка SKU, колонка оборота) или бросает SalesParseError."""
    cols = list(df.columns)

    def norm(c: object) -> str:
        return re.sub(r"\s+", " ", str(c)).strip().lower()

    sku_col = next((c for c in cols if norm(c) in _SKU_HEADERS), None)
    sales_col = next((c for c in cols if norm(c) in _SALES_HEADERS), None)

    if sku_col is None:
        scored = sorted(((_looks_like_sku(df[c]), i, c) for i, c in enumerate(cols)),
                        reverse=True)
        best = scored[0]
        if best[0] >= 0.8:
            sku_col = best[2]
    if sku_col is None:
        raise SalesParseError(
            "Не нашли колонку с кодами товаров: ожидается колонка «КодТовара»/«SKU» "
            "или колонка, целиком состоящая из числовых кодов."
        )
    if sales_col is None:
        candidates = [c for c in cols if c != sku_col]
        scored = sorted(((_numeric_share(df[c]), i, c) for i, c in enumerate(candidates)),
                        reverse=True)
        if scored and scored[0][0] >= 0.8:
            sales_col = scored[0][2]
    if sales_col is None:
        raise SalesParseError(
            "Не нашли числовую колонку с оборотом: ожидается колонка "
            "«Оборот»/«Продажи» с суммами продаж."
        )
    return sku_col, sales_col


def _read_csv_flexible(text: str) -> pd.DataFrame:
    """Читает CSV с автоопределением разделителя и наличия заголовка."""
    sample_lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if not sample_lines:
        raise SalesParseError("Файл продаж пуст.")
    sample = "\n".join(sample_lines)
    delimiter = max([",", ";", "\t"], key=sample.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise SalesParseError("Файл продаж пуст.")
    # заголовок есть, если в первой строке ни одно поле не похоже на число
    first = rows[0]
    has_header = all(parse_number(c) is None for c in first if c.strip())
    if has_header and len(rows) == 1:
        raise SalesParseError("В файле продаж только заголовок, данных нет.")
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if has_header:
        header = [c.strip() or f"col{i}" for i, c in enumerate(first)]
        return pd.DataFrame(rows[1:], columns=header[:width] + [f"col{i}" for i in range(len(first), width)])
    return pd.DataFrame(rows, columns=[f"col{i}" for i in range(width)])


def parse_sales(source: str | Path | bytes, source_name: str = "") -> SalesData:
    """Читает файл продаж (путь или байты) и возвращает :class:`SalesData`."""
    if isinstance(source, (str, Path)):
        source_name = source_name or Path(source).name
        raw = Path(source).read_bytes()
    else:
        raw = source
    if not raw.strip():
        raise SalesParseError("Файл продаж пуст.")

    warnings: list[str] = []
    if raw[:4] == _XLSX_MAGIC or source_name.lower().endswith((".xlsx", ".xlsm")):
        try:
            table = pd.read_excel(io.BytesIO(raw), sheet_name=0, dtype=object)
        except Exception as exc:  # noqa: BLE001 — превращаем в понятную ошибку
            raise SalesParseError(f"Не удалось прочитать Excel-файл продаж: {exc}") from exc
        if table.empty:
            raise SalesParseError("Первый лист Excel-файла продаж пуст.")
        # если заголовка не было, pandas сделал данными имена Unnamed — вернём строку
        if all(str(c).startswith("Unnamed") for c in table.columns):
            table.columns = [f"col{i}" for i in range(table.shape[1])]
    else:
        try:
            text, _ = decode_bytes(raw)
        except Exception as exc:  # noqa: BLE001
            raise SalesParseError(str(exc)) from exc
        table = _read_csv_flexible(text)

    sku_col, sales_col = _detect_columns(table)
    df = pd.DataFrame({
        "sku": table[sku_col].astype(str).str.strip()
                            .str.replace(r"^(\d+)\.0$", r"\1", regex=True),
        "sales": to_number_series(table[sales_col]),
    })
    df = df[df["sku"].astype(str).str.strip() != ""]
    bad = int(df["sales"].isna().sum())
    if bad:
        warnings.append(f"Строк с нечисловым оборотом (взято 0): {bad}")
        df["sales"] = df["sales"].fillna(0.0)
    if df.empty:
        raise SalesParseError("В файле продаж не осталось строк с кодами товаров.")

    dup = int(df.duplicated("sku").sum())
    if dup:
        warnings.append(f"Дубликаты КодТовара: {dup} строк — обороты просуммированы.")
    df = df.groupby("sku", as_index=False, sort=False)["sales"].sum()
    df["sales"] = df["sales"].astype(float)

    for w in warnings:
        log.warning("%s: %s", source_name or "sales", w)
    return SalesData(df=df, warnings=warnings, source_name=source_name)
