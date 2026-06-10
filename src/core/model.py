"""Модель данных: связка CSV конкурентов + продажи, пивот «банк → ритейлеры».

Широкая таблица (``Dataset.wide``): строка = SKU, колонки = атрибуты товара +
``pay_comfy`` + ``pay::<конкурент>`` (кол-во платежей у конкурента в выбранном
банке) + ``sales`` + пересчитанные по банку ``comp_max_bank``/``dev_bank``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from core.mapping import BrandOverride, ComfyMapping
from parsers.competitors import CompetitorsData, competitor_display_name
from parsers.sales import SalesData

#: специальное значение переключателя банка: агрегация max по всем банкам
ALL_BANKS = "Все банки"

#: префикс колонок платежей конкурентов в широкой таблице
PAY_PREFIX = "pay::"

#: колонка платежей Comfy в широкой таблице
PAY_COMFY = "pay_comfy"

#: атрибуты SKU, постоянные внутри кода товара
SKU_ATTRS = [
    "name", "brand", "subcategory", "category", "business", "attr_rma",
    "supplier_item", "stock_comfy", "stock_sp", "discount", "price", "margin",
    "competitors_max", "comfy_max", "deviation_max", "step",
    "assort_type", "assort_type_global",
]


def pay_col(competitor: str) -> str:
    return f"{PAY_PREFIX}{competitor}"


@dataclass
class Dataset:
    """Загруженные данные: конкуренты (обязательно) и продажи (опционально)."""

    comp: CompetitorsData
    sales: SalesData | None = None
    sales_warnings: list[str] = field(default_factory=list)

    @property
    def banks(self) -> list[str]:
        return list(self.comp.banks)

    @property
    def competitors(self) -> list[str]:
        return list(self.comp.competitors)

    @property
    def has_sales(self) -> bool:
        return self.sales is not None

    def attach_sales(self, sales: SalesData) -> "Dataset":
        """Подключает файл продаж; SKU, отсутствующие в CSV конкурентов, игнорируются."""
        known = set(self.comp.long["sku"])
        extra = [s for s in sales.df["sku"] if s not in known]
        warnings = list(sales.warnings)
        if extra:
            sample = ", ".join(extra[:10]) + ("…" if len(extra) > 10 else "")
            warnings.append(
                f"SKU из файла продаж отсутствуют в CSV конкурентов и игнорируются: "
                f"{len(extra)} шт. ({sample})"
            )
        return replace(self, sales=sales, sales_warnings=warnings)

    def wide(
        self,
        bank: str,
        mapping: ComfyMapping | None = None,
        brand_override: BrandOverride | None = None,
    ) -> pd.DataFrame:
        """Широкая таблица по выбранному банку (или :data:`ALL_BANKS`).

        Если ``mapping`` задан и применяется к этому банку, платежи Comfy
        (`pay_comfy`) пересчитываются через таблицу «макс. доступность →
        доступность в банке»; «Все банки» и остальные банки не затрагиваются.

        ``brand_override`` (обычно Apple) перезаписывает платежи Comfy для
        SKU бренда значением, заданным для банка вручную, — приоритетнее
        маппинга. В режиме «Все банки» берётся max по банкам: для банков без
        переопределения — значение из CSV.
        """
        long = self.comp.long
        sub = long if bank == ALL_BANKS else long[long["bank"] == bank]

        attrs = (
            long.groupby("sku", sort=False)[SKU_ATTRS]
            .first()  # first() пропускает NA — берём первое заполненное значение
        )

        if len(sub):
            pivot = sub.pivot_table(
                index="sku", columns="competitor", values="payments", aggfunc="max"
            )
        else:
            pivot = pd.DataFrame(index=attrs.index)
        pivot = pivot.rename(columns={c: pay_col(c) for c in pivot.columns})
        for comp in self.competitors:  # все конкуренты файла, даже пустые в этом банке
            if pay_col(comp) not in pivot.columns:
                pivot[pay_col(comp)] = pd.NA
        pivot = pivot[[pay_col(c) for c in self.competitors]].astype("Float64")

        wide = attrs.join(pivot, how="left")
        if mapping is not None and mapping.applies_to(bank):
            wide[PAY_COMFY] = mapping.apply(wide["comfy_max"])
        else:
            wide[PAY_COMFY] = wide["comfy_max"].astype("Float64")

        if brand_override is not None and brand_override.is_active:
            mask = brand_override.matches(wide["brand"])
            if mask.any():
                if bank == ALL_BANKS:
                    # max по банкам: переопределённые значения против CSV
                    # для банков, оставшихся без переопределения
                    best = float(max(brand_override.per_bank.values()))
                    if set(self.banks) <= set(brand_override.per_bank):
                        wide.loc[mask, PAY_COMFY] = best
                    else:
                        current = wide.loc[mask, PAY_COMFY]
                        wide.loc[mask, PAY_COMFY] = current.where(
                            current >= best, best
                        )
                else:
                    value = brand_override.value_for(bank)
                    if value is not None:
                        wide.loc[mask, PAY_COMFY] = float(value)

        comp_cols = [pay_col(c) for c in self.competitors]
        if comp_cols:
            comp_max = wide[comp_cols].max(axis=1)
        else:
            comp_max = pd.Series(pd.NA, index=wide.index, dtype="Float64")
        wide["comp_max_bank"] = comp_max
        wide["dev_bank"] = wide[PAY_COMFY] - comp_max

        if self.sales is not None:
            sales_map = self.sales.df.set_index("sku")["sales"]
            wide["sales"] = wide.index.map(sales_map).astype("Float64").fillna(0.0)
        else:
            wide["sales"] = pd.array([0.0] * len(wide), dtype="Float64")

        return wide.reset_index()

    def competitor_names(self) -> dict[str, str]:
        """domain → отображаемое имя (`foxtrot.com.ua` → `Foxtrot`)."""
        names: dict[str, str] = {}
        for comp in self.competitors:
            disp = competitor_display_name(comp)
            # защита от коллизий отображаемых имён
            if disp in names.values():
                disp = comp
            names[comp] = disp
        return names


@dataclass
class Filters:
    """Мультивыбор по классификатору + поиск; None — фильтр не активен.

    ``complete_competitors_only`` — оставить только SKU, по которым есть
    данные у **всех** конкурентов файла (в выбранном банке): сравнение
    идёт по общему набору товаров.
    """

    business: set[str] | None = None
    category: set[str] | None = None
    subcategory: set[str] | None = None
    assort_type: set[str] | None = None
    assort_type_global: set[str] | None = None
    brand: set[str] | None = None
    search: str = ""
    complete_competitors_only: bool = False

    _FIELD_TO_COLUMN = {
        "business": "business",
        "category": "category",
        "subcategory": "subcategory",
        "assort_type": "assort_type",
        "assort_type_global": "assort_type_global",
        "brand": "brand",
    }

    @property
    def active_count(self) -> int:
        n = sum(1 for f in self._FIELD_TO_COLUMN if getattr(self, f))
        return n + (1 if self.search.strip() else 0) \
            + (1 if self.complete_competitors_only else 0)

    def apply(self, wide: pd.DataFrame) -> pd.DataFrame:
        """Возвращает отфильтрованную широкую таблицу (копия не делается)."""
        mask = pd.Series(True, index=wide.index)
        for fld, col in self._FIELD_TO_COLUMN.items():
            selected = getattr(self, fld)
            if selected:
                mask &= wide[col].isin(selected)
        text = self.search.strip().lower()
        if text:
            mask &= (
                wide["name"].astype(str).str.lower().str.contains(text, regex=False)
                | wide["sku"].astype(str).str.contains(text, regex=False)
            )
        if self.complete_competitors_only:
            comp_cols = [c for c in wide.columns if c.startswith(PAY_PREFIX)]
            if comp_cols:
                mask &= wide[comp_cols].notna().all(axis=1)
        return wide[mask]

    def describe(self) -> str:
        """Краткое описание активных фильтров для шапки отчёта."""
        parts: list[str] = []
        labels = {
            "business": "Бизнес", "category": "Категория", "subcategory": "Подкатегория",
            "assort_type": "Тип ассортимента", "assort_type_global": "ТипАссортиментаОбщий",
            "brand": "Бренд",
        }
        for fld, label in labels.items():
            selected = getattr(self, fld)
            if selected:
                parts.append(f"{label}: {', '.join(sorted(selected))}")
        if self.search.strip():
            parts.append(f"Поиск: «{self.search.strip()}»")
        if self.complete_competitors_only:
            parts.append("только SKU, представленные у всех конкурентов")
        return "; ".join(parts) if parts else "без фильтров"
