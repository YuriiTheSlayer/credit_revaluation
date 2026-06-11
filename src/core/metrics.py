"""Расчёт метрик: структура (вес внутри категории), средневзвешенный срок
рассрочки (SUMPRODUCT) с перенормировкой весов, отклонения.

Соглашения (README §3):

* вес товара считается **внутри категории**: ``w_i = v_i / Σ v (по категории)``,
  где ``v`` — продажи или цена; расчёт ведётся по уже отфильтрованному набору
  SKU, поэтому при активных фильтрах веса перенормируются автоматически;
* SKU с пустым значением платежей у ритейлера исключается из расчёта этого
  ритейлера, а веса оставшихся перенормируются (сумма = 1) — иначе средняя
  занижается. Эквивалентная формула: ``Σ(w·pay) / Σ(w · [pay есть])``.
"""

from __future__ import annotations

import pandas as pd


def structure(wide: pd.DataFrame, value_col: str, by: str = "category") -> pd.Series:
    """Доля SKU внутри категории по колонке ``value_col`` (продажи или цена).

    Пустые значения трактуются как 0. Если сумма по категории равна 0,
    все доли категории равны 0.
    """
    values = wide[value_col].astype("Float64").fillna(0.0).astype(float)
    group_sum = values.groupby(wide[by]).transform("sum")
    share = values.divide(group_sum.where(group_sum != 0))
    return share.fillna(0.0)


def weighted_terms(
    wide: pd.DataFrame,
    value_col: str,
    pay_cols: list[str],
    by: str = "category",
) -> pd.DataFrame:
    """Средневзвешенный срок (кол-во платежей) по категориям и ритейлерам.

    Возвращает DataFrame: индекс — категория, колонки — ``pay_cols``.
    NaN — у ритейлера нет данных ни по одному SKU категории с ненулевым весом
    (или у категории нулевая база весов).
    """
    weights = structure(wide, value_col, by=by)
    out: dict[str, pd.Series] = {}
    groups = wide[by]
    for col in pay_cols:
        pay = wide[col].astype("Float64").astype(float)  # NaN там, где данных нет
        has_data = pay.notna()
        numerator = (weights * pay).where(has_data, 0.0).groupby(groups).sum()
        denominator = weights.where(has_data, 0.0).groupby(groups).sum()
        out[col] = numerator.divide(denominator.where(denominator > 0))
    return pd.DataFrame(out)


def overall_terms(
    wide: pd.DataFrame, value_col: str, pay_cols: list[str]
) -> pd.Series:
    """Средневзвешенный срок по всему (отфильтрованному) набору SKU.

    Веса нормируются по всему набору — используется для KPI-карточек,
    когда выбрано несколько категорий.
    """
    if not len(wide):
        return pd.Series({c: float("nan") for c in pay_cols})
    tmp = wide.copy()
    tmp["__all__"] = "all"
    res = weighted_terms(tmp, value_col, pay_cols, by="__all__")
    if res.empty:
        return pd.Series({c: float("nan") for c in pay_cols})
    return res.iloc[0]


def average_terms_and_payments(
    wide: pd.DataFrame,
    pay_cols: list[str],
    by: str = "category",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Третий разрез отчёта: невзвешенные средние внутри категории.

    Для каждого ритейлера (Comfy и конкуренты) в разрезе выбранного банка:

    * **средний срок** — простое среднее «кол-во платежей» по SKU категории,
      у которых есть данные этого ритейлера;
    * **средний платёж** — средняя стоимость / средний срок; средняя
      стоимость считается по той же базе SKU, что и срок ритейлера, чтобы
      числитель и знаменатель относились к одному набору товаров.

    Возвращает (terms, payments): DataFrame, индекс — категория,
    колонки — ``pay_cols``. NaN — у ритейлера нет данных в категории.
    """
    groups = wide[by]
    price = wide["price"].astype("Float64").astype(float)
    terms: dict[str, pd.Series] = {}
    payments: dict[str, pd.Series] = {}
    for col in pay_cols:
        pay = wide[col].astype("Float64").astype(float)
        has_data = pay.notna()
        term_mean = pay[has_data].groupby(groups[has_data]).mean()
        price_mean = price[has_data].groupby(groups[has_data]).mean()
        terms[col] = term_mean
        payments[col] = price_mean.divide(term_mean.where(term_mean > 0))
    return pd.DataFrame(terms), pd.DataFrame(payments)


def market_terms_and_payments(
    wide: pd.DataFrame,
    comp_cols: list[str],
    by: str = "category",
) -> tuple[pd.Series, pd.Series]:
    """Сводный «рынок»: средний срок и платёж по всем конкурентам банка разом.

    Наблюдение — пара SKU × конкурент с данными: каждый оффер конкурента
    весит одинаково. Средний срок — среднее всех наблюдений категории;
    средний платёж — средняя цена тех же наблюдений / средний срок
    (цена SKU учитывается столько раз, у скольких конкурентов он есть, —
    числитель и знаменатель по одной базе).

    Возвращает (terms, payments): Series с индексом-категорией.
    """
    price = wide["price"].astype("Float64").astype(float)
    parts = []
    for col in comp_cols:
        pay = wide[col].astype("Float64").astype(float)
        mask = pay.notna()
        if mask.any():
            parts.append(pd.DataFrame({
                "group": wide.loc[mask, by],
                "pay": pay[mask],
                "price": price[mask],
            }))
    if not parts:
        empty = pd.Series(dtype=float)
        return empty, empty
    long = pd.concat(parts, ignore_index=True)
    terms = long.groupby("group")["pay"].mean()
    price_mean = long.groupby("group")["price"].mean()
    payments = price_mean.divide(terms.where(terms > 0))
    terms.index.name = by
    payments.index.name = by
    return terms, payments


def win_share(wide: pd.DataFrame, by: str = "category") -> pd.Series:
    """Доля SKU, где Comfy ≥ лучшего конкурента (по выбранному банку).

    Считается среди SKU, у которых есть данные хотя бы по одному конкуренту.
    """
    has_comp = wide["comp_max_bank"].notna() & wide["pay_comfy"].notna()
    sub = wide[has_comp]
    if not len(sub):
        return pd.Series(dtype=float)
    wins = (sub["pay_comfy"] >= sub["comp_max_bank"]).astype(float)
    return wins.groupby(sub[by]).mean()


def deviation_distribution(wide: pd.DataFrame) -> pd.Series:
    """Распределение отклонений Comfy − max(конкуренты): значение → кол-во SKU."""
    dev = wide["dev_bank"].dropna().astype(float)
    if not len(dev):
        return pd.Series(dtype=int)
    return dev.value_counts().sort_index()
