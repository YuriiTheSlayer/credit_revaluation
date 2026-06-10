"""Парсер CSV-выгрузки условий рассрочки по конкурентам (формат «Книга31.csv»).

Особенности входного формата (README §2.1):

* кодировка Windows-1251 (бывает UTF-8) — автоопределение;
* первая строка — заголовок из 22 колонок, в конце строки «;»;
* вторая строка — служебная (``,,,,...,25;``) — пропускается;
* каждая строка данных имеет «двойную» структуру::

      КодТовара,Название;"КодТовара,Бренд,...,max(КолвоПлатежей)"

  Внутренний блок обёрнут в кавычки, кавычки внутри него удвоены (RFC-4180),
  и после развёртки он сам является корректным CSV-фрагментом, начинающимся
  с повтора кода товара;
* числа в «европейском» формате: NBSP/пробел — разделитель тысяч (``6 649``),
  запятая — десятичный разделитель (``14,3%``), ``-`` — пустое значение.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


class CompetitorsParseError(ValueError):
    """Файл конкурентов не может быть разобран; message — понятное объяснение."""


#: канонический заголовок CSV → внутреннее имя колонки
COLUMN_MAP: dict[str, str] = {
    "КодТовара": "sku",
    "Товар": "name",
    "Бренд": "brand",
    "Подкатегория": "subcategory",
    "Категория": "category",
    "Бизнес": "business",
    "Атрибут_RMA": "attr_rma",
    "Товар поставщика": "supplier_item",
    "Остаток_Comfy": "stock_comfy",
    "Остаток_SP": "stock_sp",
    "Скидка": "discount",
    "Цена Comfy *": "price",
    "Маржа кредитная": "margin",
    "Конкуренты. MAX платежей": "competitors_max",
    "Comfy. MAX платежей": "comfy_max",
    "Откл. MAX платежей": "deviation_max",
    "Шаг": "step",
    "Тип ассортимента": "assort_type",
    "ТипАссортиментаОбщий": "assort_type_global",
    "Банк": "bank",
    "Конкурент": "competitor",
    "max(КолвоПлатежей)": "payments",
}

#: без этих колонок расчёт невозможен
REQUIRED_COLUMNS = {
    "КодТовара", "Товар", "Категория", "Цена Comfy *",
    "Comfy. MAX платежей", "Банк", "Конкурент", "max(КолвоПлатежей)",
}

_NUMERIC_FLOAT = ("discount", "price", "step")
_NUMERIC_INT = ("competitors_max", "comfy_max", "deviation_max", "payments")
_PLACEHOLDERS = {"", "-", "–", "—", "n/a", "null", "none", "nan"}

#: строка данных: «sku,название;"внутренний блок"» (допускаем хвостовой «;»)
_DATA_RE = re.compile(r'^(?P<sku>[^,]*),(?P<name>.*?);"(?P<inner>.*)"\s*;?\s*$', re.S)


@dataclass
class CompetitorsData:
    """Результат разбора CSV конкурентов."""

    long: pd.DataFrame                      # строка = SKU × Банк × Конкурент
    banks: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    encoding: str = ""
    source_name: str = ""

    @property
    def sku_count(self) -> int:
        return int(self.long["sku"].nunique()) if len(self.long) else 0


# ---------------------------------------------------------------------------
# низкоуровневые помощники
# ---------------------------------------------------------------------------

def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Декодирует байты файла, автоопределяя cp1251/UTF-8. → (текст, кодировка)."""
    if raw[:4].startswith(b"PK\x03\x04"):
        raise CompetitorsParseError(
            "Файл похож на Excel (.xlsx), а не на CSV. Сохраните выгрузку как CSV "
            "или выберите другой файл."
        )
    if b"\x00" in raw[:4096]:
        raise CompetitorsParseError(
            "Файл бинарный или в неподдерживаемой кодировке (обнаружены нулевые "
            "байты). Ожидается текстовый CSV в Windows-1251 или UTF-8."
        )
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace"), "cp1251"


def normalize_header(cell: str) -> str:
    """Нормализует имя колонки: схлопывает пробелы (`Comfy.   MAX` → `Comfy. MAX`)."""
    return re.sub(r"\s+", " ", cell).strip().strip(";").strip()


def parse_number(value: object) -> float | None:
    """«Европейское» число → float: `6 649` → 6649, `14,3` → 14.3, `-` → None."""
    if value is None:
        return None
    s = str(value).strip().replace("\xa0", "").replace(" ", "")
    if s.lower() in _PLACEHOLDERS:
        return None
    s = s.replace("%", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_fraction(value: object) -> float | None:
    """Процент → доля: `14,3%` → 0.143. Число без знака % тоже трактуем как %."""
    num = parse_number(value)
    return None if num is None else num / 100.0


def to_number_series(s: pd.Series) -> pd.Series:
    """Векторный аналог :func:`parse_number` для больших файлов (90k+ строк).

    Минимум проходов по данным: одна regex-чистка (пробелы/NBSP/%), замена
    десятичной запятой и ``to_numeric``; плейсхолдеры («-», «n/a», …) сами
    превращаются в NaN на этапе коэрции.
    """
    cleaned = (
        s.astype(str)
        .str.replace(r"[\s\xa0%]+", "", regex=True)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def normalize_product_name(name: str, sku: str) -> str:
    """Отрезает хвост `;КодТовара` у названия товара, если он есть."""
    name = name.strip()
    if sku and name.endswith(f";{sku}"):
        name = name[: -len(sku) - 1]
    return name.rstrip(";").strip()


def _canon_sku(value: object) -> str:
    """Код товара → строка без артефактов float («20671.0» → «20671»)."""
    s = str(value).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def _unfold_line(line: str, n_cols: int) -> list[str] | None:
    """Разворачивает строку данных в плоский список из n_cols полей.

    Сначала пытаемся разобрать «двойную» структуру с вложенным quoted-блоком,
    затем — обычную плоскую CSV-строку. None — строка не распознана.
    """
    m = _DATA_RE.match(line)
    if m:
        inner = m.group("inner").replace('""', '"')
        try:
            fields = next(csv.reader(io.StringIO(inner)))
        except (csv.Error, StopIteration):
            fields = None
        # внутренний блок = повтор КодТовара + все колонки после «Товар»
        if fields is not None and len(fields) == n_cols - 1:
            return [m.group("sku"), m.group("name"), *fields[1:]]
    try:
        flat = next(csv.reader(io.StringIO(line)))
    except (csv.Error, StopIteration):
        return None
    if len(flat) == n_cols:
        return [f.strip(";") if i == n_cols - 1 else f for i, f in enumerate(flat)]
    return None


def _is_service_row(fields: list[str]) -> bool:
    """Служебная строка: ключевые поля пустые (например ``,,,,...,25;``)."""
    non_empty = [f for f in fields if f.strip().strip(";")]
    return not fields[0].strip() and len(non_empty) <= 2


# ---------------------------------------------------------------------------
# основной парсер
# ---------------------------------------------------------------------------

def parse_competitors_csv(
    source: str | Path | bytes,
    source_name: str = "",
) -> CompetitorsData:
    """Читает CSV конкурентов (путь или байты) и возвращает :class:`CompetitorsData`."""
    if isinstance(source, (str, Path)):
        source_name = source_name or Path(source).name
        raw = Path(source).read_bytes()
    else:
        raw = source
    if not raw.strip():
        raise CompetitorsParseError("Файл пуст.")

    text, encoding = decode_bytes(raw)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise CompetitorsParseError("В файле нет ни одной строки данных.")

    header = [normalize_header(c) for c in next(csv.reader(io.StringIO(lines[0])))]
    missing = REQUIRED_COLUMNS - set(header)
    if missing:
        raise CompetitorsParseError(
            "В файле нет обязательных колонок: " + ", ".join(sorted(missing)) +
            ". Проверьте, что выбрана выгрузка по конкурентам "
            "(первая строка должна содержать: " + ", ".join(sorted(REQUIRED_COLUMNS)) + ")."
        )
    unknown = [c for c in header if c not in COLUMN_MAP]
    warnings: list[str] = []
    if unknown:
        warnings.append("Неизвестные колонки проигнорированы: " + ", ".join(unknown))

    n_cols = len(header)
    bad_lines: list[int] = []
    skipped_service = 0
    sku_mismatches = 0

    # Один проход по строкам: совпавшие с «двойной» структурой откладываем для
    # пакетного разбора единым csv.reader (C-скорость), остальные — фолбэк.
    records: list[list[str] | None] = []
    inner_meta: list[tuple[int, int, str, str]] = []   # (поз., №строки, sku, name)
    inners: list[str] = []
    for idx, line in enumerate(lines[1:], start=2):
        m = _DATA_RE.match(line)
        if m:
            inner_meta.append((len(records), idx, m.group("sku"), m.group("name")))
            inners.append(m.group("inner").replace('""', '"'))
            records.append(None)
            continue
        fields = _unfold_line(line, n_cols)
        if fields is None:
            if _is_service_row(line.split(",")):
                skipped_service += 1
            else:
                bad_lines.append(idx)
        elif _is_service_row(fields):
            skipped_service += 1
        else:
            records.append(fields)

    for (pos, idx, sku, name), fields in zip(inner_meta, csv.reader(inners)):
        # внутренний блок = повтор КодТовара + все колонки после «Товар»
        if len(fields) == n_cols - 1:
            if fields[0].strip() != sku.strip():
                sku_mismatches += 1
            records[pos] = [sku, name, *fields[1:]]
        else:
            bad_lines.append(idx)

    # служебные строки не совпадают с _DATA_RE и отсеяны в фолбэк-ветке,
    # поэтому здесь достаточно убрать нераспознанные
    rows = [r for r in records if r is not None]

    if not rows:
        raise CompetitorsParseError(
            "Не удалось разобрать ни одной строки данных — структура файла "
            "не соответствует ожидаемому формату выгрузки по конкурентам."
        )
    if bad_lines:
        bad_lines.sort()
        sample = ", ".join(map(str, bad_lines[:10]))
        warnings.append(
            f"Пропущено нераспознанных строк: {len(bad_lines)} (номера: {sample}"
            + ("…" if len(bad_lines) > 10 else "") + ")"
        )
    if sku_mismatches:
        warnings.append(
            f"В {sku_mismatches} строках код товара во вложенном блоке не совпал "
            "с внешним — взято внешнее значение."
        )

    df = pd.DataFrame(rows, columns=header)
    # неизвестные колонки отбрасываем, известные переименовываем во внутренние
    df = df[[c for c in header if c in COLUMN_MAP]].rename(columns=COLUMN_MAP)
    for col in COLUMN_MAP.values():
        if col not in df.columns:
            df[col] = ""

    # дальше — только векторные операции: на 90k+ строк поэлементные вызовы
    # Python заметно медленнее
    df["sku"] = df["sku"].str.strip().str.replace(r"^(\d+)\.0$", r"\1", regex=True)
    # «Название;КодТовара» → отрезаем хвост, только если он равен коду строки
    names = df["name"].str.strip()
    parts = names.str.rsplit(";", n=1, expand=True)
    if parts.shape[1] == 2:
        tail_is_sku = parts[1].notna() & parts[1].eq(df["sku"])
        names = parts[0].str.strip().where(tail_is_sku, names)
    df["name"] = names

    for col in ("brand", "subcategory", "category", "business", "attr_rma",
                "supplier_item", "stock_comfy", "stock_sp", "assort_type",
                "assort_type_global", "bank", "competitor"):
        df[col] = df[col].str.strip()

    for col in _NUMERIC_FLOAT:
        df[col] = to_number_series(df[col]).astype("Float64")
    df["margin"] = (to_number_series(df["margin"]) / 100.0).astype("Float64")
    for col in _NUMERIC_INT:
        df[col] = to_number_series(df[col]).round().astype("Int64")

    before = len(df)
    df = df[(df["sku"] != "") & (df["bank"] != "") & (df["competitor"] != "")]
    dropped = before - len(df)
    if dropped:
        warnings.append(
            f"Отброшено строк без кода товара/банка/конкурента: {dropped}"
        )

    # дубликаты SKU × Банк × Конкурент → max(платежей), предупреждение в лог
    key = ["sku", "bank", "competitor"]
    dup_count = int(df.duplicated(key).sum())
    if dup_count:
        warnings.append(
            f"Дубликаты SKU×Банк×Конкурент: {dup_count} строк — взят max(КолвоПлатежей)."
        )
        attr_cols = [c for c in df.columns if c not in key + ["payments"]]
        df = (
            df.groupby(key, as_index=False, sort=False)
            .agg({**{c: "first" for c in attr_cols}, "payments": "max"})
        )

    df = df.reset_index(drop=True)
    banks = list(dict.fromkeys(df["bank"])) if len(df) else []
    competitors = list(dict.fromkeys(df["competitor"])) if len(df) else []

    for w in warnings:
        log.warning("%s: %s", source_name or "competitors.csv", w)

    return CompetitorsData(
        long=df,
        banks=banks,
        competitors=competitors,
        warnings=warnings,
        encoding=encoding,
        source_name=source_name,
    )


def competitor_display_name(domain: str) -> str:
    """Человекочитаемое имя ритейлера: `foxtrot.com.ua` → `Foxtrot`."""
    label = domain.strip().split("/")[0].split(".")[0] or domain.strip()
    return label[:1].upper() + label[1:]
