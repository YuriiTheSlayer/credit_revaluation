"""Тесты предлагаемого кредитного маяка: матрица соответствия, ТОП банки,
Spush (max) против прочих (среднее), влияние переопределения бренда."""

import pandas as pd
import pytest

from core.beacon import LADDER, compute_beacons, default_top_banks, ladder_up
from core.mapping import BrandOverride
from core.model import Dataset
from parsers.competitors import parse_competitors_csv

#: «Матрица соответствия КМ» с фото: рынок → предлагаемый маяк Comfy
MATRIX = {
    3: 3, 4: 5, 5: 5, 6: 7, 7: 7, 8: 10, 9: 10, 10: 10, 11: 12, 12: 12,
    13: 15, 14: 15, 15: 15, 16: 18, 17: 18, 18: 18, 19: 20, 20: 20,
    21: 22, 22: 22, 23: 25, 24: 25, 25: 25,
}

HEADER = (
    "КодТовара,Товар,Бренд,Подкатегория,Категория,Бизнес,Атрибут_RMA,"
    "Товар поставщика,Остаток_Comfy,Остаток_SP,Скидка,Цена Comfy *,"
    "Маржа кредитная,Конкуренты. MAX платежей,Comfy.   MAX платежей,"
    "Откл. MAX платежей,Шаг,Тип ассортимента,ТипАссортиментаОбщий,"
    "Банк,Конкурент,max(КолвоПлатежей);"
)


def make_csv(rows: list[tuple]) -> bytes:
    """rows: (sku, assort_global, bank, competitor, payments, comfy_max)."""
    lines = [HEADER]
    for sku, assort_global, bank, comp, pay, comfy in rows:
        inner = (
            f'{sku},БрендХ,Подкат,Категория Х,Бизнес,Normal,Нет,Есть,Есть,0,'
            f'1\xa0000,""10,0%"",20,{comfy},-10,2,Матрица,{assort_global},'
            f'{bank},{comp},{pay}'
        )
        lines.append(f'{sku},Товар {sku};"{inner}"')
    return "\r\n".join(lines).encode("utf-8")


def test_ladder_matches_matrix_photo():
    values = pd.Series(list(MATRIX.keys()), dtype=float)
    expected = list(MATRIX.values())
    assert ladder_up(values).tolist() == expected


def test_ladder_edges_and_fractions():
    s = pd.Series([2.0, 10.5, 13.25, 26.0, None])
    out = ladder_up(s)
    assert out.iloc[0] == 3          # ниже лестницы → минимальная ступень
    assert out.iloc[1] == 12         # дробное среднее поднимается вверх
    assert out.iloc[2] == 15
    assert out.iloc[3] == 25         # выше лестницы → максимум
    assert pd.isna(out.iloc[4])


def test_default_top_banks_excludes_mono():
    assert default_top_banks(["Monobank", "ПриватБанк", "ПУМБ"]) == \
        ["ПриватБанк", "ПУМБ"]
    assert default_top_banks(["ПриватБанк", "ПУМБ"]) == ["ПриватБанк", "ПУМБ"]
    assert default_top_banks(["Monobank"]) == ["Monobank"]   # фолбэк


def test_beacons_on_fixture(kniga_path):
    ds = Dataset(parse_competitors_csv(kniga_path))
    beacons = compute_beacons(ds.comp.long, ds.banks)

    # 20671: Приват fox 15, ПУМБ fox 15 → рынок 15 → предлагаемый 15
    assert beacons.loc["20671", "beacon_market"] == pytest.approx(15)
    assert beacons.loc["20671", "beacon_proposed"] == 15
    assert beacons.loc["20671", "beacon_comfy"] == pytest.approx(10)

    # 925595: fox (3+3)/2=3, roz 6 → рынок 4.5 → предлагаемый 5
    assert beacons.loc["925595", "beacon_market"] == pytest.approx(4.5)
    assert beacons.loc["925595", "beacon_proposed"] == 5

    # 913162: fox (10+3)/2=6.5, roz (6+6)/2=6 → рынок 6.25 → предлагаемый 7
    assert beacons.loc["913162", "beacon_market"] == pytest.approx(6.25)
    assert beacons.loc["913162", "beacon_proposed"] == 7


def test_spush_takes_max_others_take_mean():
    rows = [
        # Spush: fox Приват 10, ПУМБ 12; roz Приват 16 → max = 16 → 18
        (111, "Spush", "ПриватБанк", "foxtrot.com.ua", 10, 10),
        (111, "Spush", "ПУМБ", "foxtrot.com.ua", 12, 10),
        (111, "Spush", "ПриватБанк", "rozetka.com.ua", 16, 10),
        # те же платежи, но не Spush: fox (10+12)/2=11, roz 16 → 13.5 → 15
        (222, "Додатковий", "ПриватБанк", "foxtrot.com.ua", 10, 10),
        (222, "Додатковий", "ПУМБ", "foxtrot.com.ua", 12, 10),
        (222, "Додатковий", "ПриватБанк", "rozetka.com.ua", 16, 10),
    ]
    ds = Dataset(parse_competitors_csv(make_csv(rows)))
    beacons = compute_beacons(ds.comp.long, ds.banks)

    assert beacons.loc["111", "beacon_market"] == pytest.approx(16)
    assert beacons.loc["111", "beacon_proposed"] == 18
    assert beacons.loc["222", "beacon_market"] == pytest.approx(13.5)
    assert beacons.loc["222", "beacon_proposed"] == 15


def test_spush_market_ignores_mono_bank():
    rows = [
        # у Monobank срок длиннее, но он не ТОП банк — не участвует
        (111, "Spush", "Monobank", "foxtrot.com.ua", 25, 10),
        (111, "Spush", "ПриватБанк", "foxtrot.com.ua", 10, 10),
    ]
    ds = Dataset(parse_competitors_csv(make_csv(rows)))
    beacons = compute_beacons(ds.comp.long, ds.banks)
    assert beacons.loc["111", "beacon_market"] == pytest.approx(10)
    assert beacons.loc["111", "beacon_proposed"] == 10


def test_brand_override_changes_comfy_beacon(kniga_path):
    ds = Dataset(parse_competitors_csv(kniga_path))

    override = BrandOverride(brand="Thomas",
                             per_bank={"ПриватБанк": 12, "ПУМБ": 6})
    beacons = compute_beacons(ds.comp.long, ds.banks, override)
    assert beacons.loc["20671", "beacon_comfy"] == pytest.approx(9)   # (12+6)/2
    assert beacons.loc["502865", "beacon_comfy"] == pytest.approx(7)  # чужой бренд

    mono_only = BrandOverride(brand="Thomas", per_bank={"Monobank": 3})
    beacons = compute_beacons(ds.comp.long, ds.banks, mono_only)
    assert beacons.loc["20671", "beacon_comfy"] == pytest.approx(10)  # mono не ТОП


def test_wide_contains_beacon_columns(kniga_path):
    ds = Dataset(parse_competitors_csv(kniga_path))
    wide = ds.wide("Monobank").set_index("sku")
    for col in ("beacon_comfy", "beacon_market", "beacon_proposed"):
        assert col in wide.columns
    # маяк не зависит от выбранного банка
    privat = ds.wide("ПриватБанк").set_index("sku")
    assert wide["beacon_proposed"].equals(privat["beacon_proposed"])
    assert wide.loc["20671", "beacon_proposed"] == 15


def test_ladder_is_sorted_unique():
    assert list(LADDER) == sorted(set(LADDER))
