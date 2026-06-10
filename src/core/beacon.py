"""Предлагаемый кредитный маяк (КМ) по SKU.

Логика (по постановке бизнеса):

* **ТОП банки** — банки максимальной доступности (Приват/ПУМБ); технически —
  все банки файла, кроме банка со сниженной доступностью (эвристика «mono»).
* **КМ Comfy** — текущий маяк Comfy: среднее платежей Comfy по ТОП банкам.
  Платежи Comfy в выгрузке едины для ТОП банков («Comfy. MAX платежей»),
  поэтому среднее отличается от него только при переопределении бренда
  (Apple) с разными значениями по ТОП банкам.
* **КМ рынка** в разрезе ТОП банков:
  - ``ТипАссортиментаОбщий = Spush`` → **максимум** у одного из конкурентов;
  - прочие товары → **среднее по конкурентам** (значение конкурента — среднее
    его платежей по ТОП банкам).
* **Предлагаемый КМ** — КМ рынка, поднятый по «Матрице соответствия КМ»
  вверх до ближайшей ступени лестницы сроков
  3 / 5 / 7 / 10 / 12 / 15 / 18 / 20 / 22 / 25
  (рынок 4 → 5, 8 → 10, 13 → 15, 17 → 18, 19 → 20, 23 → 25, …;
  больше 25 → 25).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.mapping import BrandOverride, guess_reduced_bank

#: лестница допустимых значений кредитного маяка («Матрица соответствия КМ»)
LADDER: tuple[int, ...] = (3, 5, 7, 10, 12, 15, 18, 20, 22, 25)

SPUSH_VALUE = "spush"


def default_top_banks(banks: list[str]) -> list[str]:
    """ТОП банки: все, кроме банка со сниженной доступностью (Monobank)."""
    reduced = guess_reduced_bank(banks)
    top = [b for b in banks if b != reduced]
    return top or list(banks)


def ladder_up(values: pd.Series) -> pd.Series:
    """Поднимает значения вверх до ближайшей ступени :data:`LADDER`."""
    arr = np.asarray(pd.to_numeric(values, errors="coerce"), dtype=float)
    ladder = np.asarray(LADDER, dtype=float)
    idx = np.searchsorted(ladder, arr, side="left")
    idx = np.clip(idx, 0, len(ladder) - 1)
    result = ladder[idx]
    result = np.where(np.isnan(arr), np.nan, result)
    return pd.Series(result, index=values.index)


def compute_beacons(
    long: pd.DataFrame,
    banks: list[str],
    brand_override: BrandOverride | None = None,
    top_banks: list[str] | None = None,
) -> pd.DataFrame:
    """Маяки по SKU: ``beacon_comfy``, ``beacon_market``, ``beacon_proposed``.

    ``long`` — длинная таблица конкурентов (SKU × Банк × Конкурент).
    Возвращает DataFrame с индексом ``sku``.
    """
    top = top_banks or default_top_banks(banks)
    attrs = long.groupby("sku", sort=False)[
        ["assort_type_global", "brand", "comfy_max"]
    ].first()

    sub = long[long["bank"].isin(top) & long["payments"].notna()]
    if len(sub):
        # значение конкурента = среднее его платежей по ТОП банкам
        per_competitor = sub.groupby(["sku", "competitor"], sort=False)["payments"].mean()
        market_mean = per_competitor.groupby(level="sku").mean()
        market_max = sub.groupby("sku")["payments"].max().astype(float)
    else:
        market_mean = pd.Series(dtype=float)
        market_max = pd.Series(dtype=float)

    spush = (
        attrs["assort_type_global"].astype(str).str.strip().str.casefold()
        .eq(SPUSH_VALUE)
    )
    market = market_mean.reindex(attrs.index)
    market = market.mask(spush, market_max.reindex(attrs.index))

    comfy = attrs["comfy_max"].astype("Float64").astype(float)
    if brand_override is not None and brand_override.is_active and top:
        known = [brand_override.per_bank[b] for b in top
                 if b in brand_override.per_bank]
        if known:
            mask = brand_override.matches(attrs["brand"])
            # среднее по ТОП банкам: заданные значения + CSV для остальных
            mean_series = (sum(known) + comfy * (len(top) - len(known))) / len(top)
            comfy = comfy.mask(mask, mean_series)

    return pd.DataFrame({
        "beacon_comfy": comfy,
        "beacon_market": market,
        "beacon_proposed": ladder_up(market),
    })
