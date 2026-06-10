"""Производительность на файле масштаба боевого: 90k строк CSV (15k SKU).

Границы взяты с ~5-кратным запасом от замеров на контейнере разработки
(parse ≈ 2.8s, wide ≈ 0.3s, metrics ≈ 0.05s, export ≈ 3s), чтобы тест ловил
квадратичные регрессии, а не дрожание железа.
"""

import time

import pytest

from core import metrics
from core.model import Dataset, PAY_COMFY, pay_col
from export.excel import export_report
from parsers.competitors import parse_competitors_csv

HEADER = (
    "КодТовара,Товар,Бренд,Подкатегория,Категория,Бизнес,Атрибут_RMA,"
    "Товар поставщика,Остаток_Comfy,Остаток_SP,Скидка,Цена Comfy *,"
    "Маржа кредитная,Конкуренты. MAX платежей,Comfy.   MAX платежей,"
    "Откл. MAX платежей,Шаг,Тип ассортимента,ТипАссортиментаОбщий,"
    "Банк,Конкурент,max(КолвоПлатежей);"
)


def synth_csv(n_sku: int = 15000) -> bytes:
    """90k строк: n_sku × 3 банка × 2 конкурента, 80 категорий."""
    banks = ("Monobank", "ПриватБанк", "ПУМБ")
    comps = ("foxtrot.com.ua", "rozetka.com.ua")
    lines = [HEADER, ",,,,,,,,,,,,,,,,,,,,,25;"]
    for i in range(n_sku):
        sku = 100000 + i
        cat = f"Категория {i % 80}"
        for b in banks:
            for c in comps:
                pay = 3 + (i * 7 + len(b) + len(c)) % 18
                inner = (
                    f'{sku},Бренд {i % 40},Подкат {i % 200},{cat},Бизнес {i % 6},'
                    f'Normal,Нет,Есть,Есть,0,6\xa0649,""14,3%"",15,10,-5,2,'
                    f'Матрица,Розничный,{b},{c},{pay}'
                )
                lines.append(f'{sku},Товар номер {sku};"{inner}"')
    return "\r\n".join(lines).encode("cp1251")


@pytest.fixture(scope="module")
def big_dataset() -> tuple[Dataset, float]:
    raw = synth_csv()
    t0 = time.perf_counter()
    data = parse_competitors_csv(raw, source_name="synth90k.csv")
    elapsed = time.perf_counter() - t0
    assert len(data.long) == 90000
    return Dataset(data), elapsed


def test_parse_90k_rows_under_15s(big_dataset):
    _, elapsed = big_dataset
    assert elapsed < 15, f"парсинг 90k строк занял {elapsed:.1f}s"


def test_pivot_and_metrics_fast(big_dataset):
    ds, _ = big_dataset
    t0 = time.perf_counter()
    wide = ds.wide("Monobank")
    t_wide = time.perf_counter() - t0
    assert len(wide) == 15000
    assert t_wide < 3, f"пивот занял {t_wide:.1f}s"

    pay_cols = [PAY_COMFY] + [pay_col(c) for c in ds.competitors]
    t0 = time.perf_counter()
    metrics.weighted_terms(wide, "price", pay_cols)
    metrics.overall_terms(wide, "price", pay_cols)
    metrics.structure(wide, "price")
    t_metrics = time.perf_counter() - t0
    assert t_metrics < 2, f"метрики заняли {t_metrics:.1f}s"


def test_export_15k_sku_under_20s(big_dataset, tmp_path):
    ds, _ = big_dataset
    wide = ds.wide("Monobank")
    out = tmp_path / "big.xlsx"
    t0 = time.perf_counter()
    export_report(out, [("Monobank", wide)], ds.competitor_names(), has_sales=False)
    elapsed = time.perf_counter() - t0
    assert elapsed < 20, f"экспорт 15k SKU занял {elapsed:.1f}s"
    assert out.stat().st_size > 100_000
