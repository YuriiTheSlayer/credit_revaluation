"""Python-API для JS-фронтенда (pywebview).

Держит всё состояние приложения (датасет, банк, веса, фильтры, маппинги,
сортировку/пагинацию таблицы) и отдаёт фронтенду готовые к рендеру снапшоты
(JSON-словарь). JS-слой — только отрисовка: вся логика здесь, поэтому она
покрывается тестами без открытия окна (``tests/test_webui_api.py``).

Методы без ведущего подчёркивания экспонируются в JS как
``window.pywebview.api.<имя>`` и возвращают свежий снапшот (плюс ``error`` /
``cancelled``, когда уместно). Диалоги выбора файлов живут в методах
``pick_*``/``export`` — их логика вынесена в ``load_*``/``export_to``,
которые тестируются напрямую.
"""

from __future__ import annotations

import math
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from core import brand, metrics
from core.mapping import (
    BRAND_OVERRIDE_FILE,
    MAPPING_FILE,
    BrandOverride,
    ComfyMapping,
    guess_reduced_bank,
)
from core.model import ALL_BANKS, Dataset, Filters, PAY_COMFY, pay_col
from export.excel import default_filename, export_report
from parsers.competitors import parse_competitors_csv
from parsers.sales import parse_sales

PAGE_SIZE = 20
CHART_CATEGORIES_LIMIT = 10

_FILTER_FIELDS = ("business", "category", "subcategory",
                  "assort_type", "assort_type_global", "brand")
_FILTER_LABELS = {
    "business": "Бизнес", "category": "Категория", "subcategory": "Подкатегория",
    "assort_type": "Тип ассортимента", "assort_type_global": "ТипАссортиментаОбщий",
    "brand": "Бренд",
}


def _val(value):
    """Значение pandas/numpy → JSON-дружелюбное (NaN/NA → None)."""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _fmt_count(n: int) -> str:
    return f"{n:,}".replace(",", " ")


class Api:
    """Состояние приложения + методы, экспонируемые в JS."""

    def __init__(self, mapping_path: Path | None = None,
                 override_path: Path | None = None):
        self._window = None
        self._mapping_path = mapping_path or MAPPING_FILE
        self._override_path = override_path or BRAND_OVERRIDE_FILE

        self.dataset: Dataset | None = None
        self.bank: str | None = None
        self.weight: str = "sales"               # 'sales' | 'price'
        self._weight_user_set = False            # явный выбор пользователя
        self.filters = Filters()
        self.mapping = ComfyMapping.load(self._mapping_path)
        self.apple = BrandOverride.load(self._override_path)
        self.sort_field = "sku"
        self.sort_asc = True
        self.page = 0
        self.warnings: list[str] = []
        self.status_competitors: str | None = None
        self.status_sales: str | None = None
        self._wide_cache: dict[str, pd.DataFrame] = {}
        self._pending_sales = None

    def attach_window(self, window) -> None:
        self._window = window

    # ------------------------------------------------------------- диалоги ОС
    def pick_competitors(self) -> dict:
        path = self._open_dialog(("CSV конкурентов (*.csv;*.txt)",))
        if not path:
            return {"cancelled": True}
        return self.load_competitors(path)

    def pick_sales(self) -> dict:
        path = self._open_dialog(("Файл продаж (*.csv;*.txt;*.xlsx;*.xlsm)",))
        if not path:
            return {"cancelled": True}
        return self.load_sales(path)

    def export(self, all_banks: bool = False) -> dict:
        if self.dataset is None:
            return self._with_error("Сначала загрузите CSV конкурентов.")
        import webview
        filename = default_filename("all_banks" if all_banks else (self.bank or "bank"))
        path = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=filename,
            file_types=("Excel (*.xlsx)",),
        )
        if not path:
            return {"cancelled": True}
        if isinstance(path, (list, tuple)):
            path = path[0]
        return self.export_to(str(path), all_banks)

    def _open_dialog(self, file_types: tuple[str, ...]) -> str | None:
        import webview
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else str(result)

    # ------------------------------------------------------------- загрузка
    def boot(self) -> dict:
        return self._snapshot()

    def load_competitors(self, path: str) -> dict:
        try:
            data = parse_competitors_csv(path, source_name=Path(path).name)
        except Exception as exc:  # noqa: BLE001 — сообщение уходит в UI
            traceback.print_exc()
            return self._with_error(str(exc))
        dataset = Dataset(data)
        if self.dataset is not None and self.dataset.sales is not None:
            dataset = dataset.attach_sales(self.dataset.sales)
        elif self._pending_sales is not None:
            dataset = dataset.attach_sales(self._pending_sales)
            self._pending_sales = None
        self.dataset = dataset
        if self.bank not in dataset.banks + [ALL_BANKS]:
            self.bank = dataset.banks[0] if dataset.banks else ALL_BANKS
        if not dataset.has_sales:
            self.weight = "price"                # авто-деградация без продаж
        elif not self._weight_user_set:
            self.weight = "sales"
        self.page = 0
        self.status_competitors = (
            f"{Path(path).name} · {data.encoding} · SKU {_fmt_count(data.sku_count)}"
            f" · банков {len(data.banks)} · конкурентов {len(data.competitors)}"
        )
        if self.mapping.bank is None:
            self.mapping.bank = guess_reduced_bank(dataset.banks)
        self.mapping.sync_values(
            sorted(int(v) for v in data.long["comfy_max"].dropna().unique()))
        self._refresh_warnings()
        self._wide_cache.clear()
        return self._snapshot()

    def load_sales(self, path: str) -> dict:
        try:
            sales = parse_sales(path, source_name=Path(path).name)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._with_error(str(exc))
        self.status_sales = (
            f"{Path(path).name} · строк {_fmt_count(len(sales.df))}"
            f" · оборот {_fmt_count(round(sales.total))}"
        )
        if self.dataset is not None:
            self.dataset = self.dataset.attach_sales(sales)
            if not self._weight_user_set:
                self.weight = "sales"            # продажи появились — вернём веса
            self._refresh_warnings()
            self._wide_cache.clear()
        else:
            self._pending_sales = sales
        return self._snapshot()

    def load_dropped(self, path: str) -> dict:
        """Файл, перетащенный в окно: xlsx → продажи; csv → конкуренты
        с фолбэком на продажи (для плоских двухколоночных csv)."""
        name = Path(path).name.lower()
        if name.endswith((".xlsx", ".xlsm")):
            return self.load_sales(path)
        result = self.load_competitors(path)
        if "error" in result:
            fallback = self.load_sales(path)
            if "error" not in fallback:
                return fallback
        return result

    def _refresh_warnings(self) -> None:
        ds = self.dataset
        self.warnings = (list(ds.comp.warnings) + list(ds.sales_warnings)
                         if ds is not None else [])

    # ------------------------------------------------------------- мутации UI
    def set_bank(self, bank: str) -> dict:
        if self.dataset and bank in self.dataset.banks + [ALL_BANKS]:
            self.bank = bank
            self.page = 0
        return self._snapshot()

    def set_weight(self, mode: str) -> dict:
        if mode == "sales" and (self.dataset is None or not self.dataset.has_sales):
            return self._with_error(
                "Сначала загрузите файл продаж — веса по продажам недоступны.")
        if mode in ("sales", "price"):
            self.weight = mode
            self._weight_user_set = True
        return self._snapshot()

    def set_filters(self, payload: dict) -> dict:
        selected = payload.get("selected", {})
        self.filters = Filters(
            **{f: (set(selected[f]) if selected.get(f) else None)
               for f in _FILTER_FIELDS},
            search=str(payload.get("search", "") or ""),
            complete_competitors_only=bool(payload.get("complete_only", False)),
        )
        self.page = 0
        return self._snapshot()

    def reset_filters(self) -> dict:
        self.filters = Filters()
        self.page = 0
        return self._snapshot()

    def set_table(self, page: int | None = None,
                  sort_field: str | None = None) -> dict:
        if sort_field:
            self.sort_asc = (not self.sort_asc
                             if sort_field == self.sort_field else True)
            self.sort_field = sort_field
            self.page = 0
        if page is not None:
            self.page = max(0, int(page))
        return self._snapshot()

    def save_mapping(self, bank: str, table: dict) -> dict:
        try:
            parsed = {int(k): int(v) for k, v in table.items()}
        except (TypeError, ValueError):
            return self._with_error("Все значения маппинга должны быть целыми числами.")
        if any(v < 0 for v in parsed.values()):
            return self._with_error("Количество платежей не может быть отрицательным.")
        self.mapping.bank = bank or self.mapping.bank
        self.mapping.table = parsed
        try:
            self.mapping.save(self._mapping_path)
        except OSError as exc:
            return self._with_error(f"Маппинг применён, но не сохранён: {exc}")
        self._wide_cache.clear()
        return self._snapshot()

    def save_apple(self, brand_name: str, per_bank: dict) -> dict:
        parsed: dict[str, int] = {}
        try:
            for bank_name, value in (per_bank or {}).items():
                text = str(value).strip()
                if text:
                    parsed[str(bank_name)] = int(text)
        except (TypeError, ValueError):
            return self._with_error(
                "Кол-во платежей должно быть целым числом (или пустым полем).")
        if any(v < 0 for v in parsed.values()):
            return self._with_error("Количество платежей не может быть отрицательным.")
        self.apple.brand = (brand_name or self.apple.brand).strip() or "Apple"
        self.apple.per_bank = parsed
        try:
            self.apple.save(self._override_path)
        except OSError as exc:
            return self._with_error(f"Настройка применена, но не сохранена: {exc}")
        self._wide_cache.clear()
        return self._snapshot()

    def export_to(self, path: str, all_banks: bool = False) -> dict:
        if self.dataset is None or self.bank is None:
            return self._with_error("Сначала загрузите CSV конкурентов.")
        try:
            ds = self.dataset
            banks = ds.banks + [ALL_BANKS] if all_banks else [self.bank]
            frames = [(b, self.filters.apply(self._wide(b))) for b in banks]
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            export_report(
                path, frames, ds.competitor_names(),
                has_sales=ds.has_sales,
                filters_desc=self.filters.describe(),
                mapping=self.mapping,
                brand_override=self.apple,
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._with_error(f"Ошибка экспорта: {exc}")
        snap = self._snapshot()
        snap["saved"] = str(path)
        return snap

    # ------------------------------------------------------------- снапшот
    def _with_error(self, message: str) -> dict:
        snap = self._snapshot()
        snap["error"] = message
        return snap

    def _wide(self, bank: str) -> pd.DataFrame:
        assert self.dataset is not None
        if bank not in self._wide_cache:
            self._wide_cache[bank] = self.dataset.wide(
                bank, self.mapping, brand_override=self.apple)
        return self._wide_cache[bank]

    def _snapshot(self) -> dict:
        base = {
            "loaded": self.dataset is not None,
            "statuses": {
                "competitors": self.status_competitors,
                "sales": self.status_sales,
            },
            "warnings": self.warnings,
            "brandTokens": {
                "green": brand.GREEN, "greenDark": brand.GREEN_DARK,
                "greenTint": brand.GREEN_TINT, "graphite": brand.GRAPHITE,
                "muted": brand.MUTED, "bg": brand.BG, "surface": brand.SURFACE,
                "outline": brand.OUTLINE, "orange": brand.ORANGE,
                "orangeTint": brand.ORANGE_TINT, "red": brand.RED,
                "redTint": brand.RED_TINT,
            },
            "appTitle": brand.APP_TITLE,
            "appSubtitle": brand.APP_SUBTITLE,
        }
        if self.dataset is None:
            base.update({
                "banks": [], "bank": None, "allBanks": ALL_BANKS,
                "weight": self.weight, "hasSales": False,
                "filters": {"options": {f: [] for f in _FILTER_FIELDS},
                            "labels": _FILTER_LABELS,
                            "selected": {f: [] for f in _FILTER_FIELDS},
                            "search": "", "completeOnly": False},
                "kpi": [], "charts": None, "table": None,
                "mapping": self._mapping_state(), "apple": self._apple_state(),
            })
            return base

        ds = self.dataset
        wide = self._wide(self.bank)
        filtered = self.filters.apply(wide)
        value_col = "sales" if self.weight == "sales" else "price"
        names = ds.competitor_names()
        pay_cols = [PAY_COMFY] + [pay_col(d) for d in names]

        base.update({
            "banks": ds.banks, "bank": self.bank, "allBanks": ALL_BANKS,
            "weight": self.weight, "hasSales": ds.has_sales,
            "filters": self._filters_state(),
            "kpi": self._kpi(filtered, value_col, names, pay_cols),
            "charts": self._charts(filtered, value_col, names, pay_cols),
            "table": self._table(filtered, value_col, names),
            "mapping": self._mapping_state(),
            "apple": self._apple_state(),
        })
        return base

    def _filters_state(self) -> dict:
        ds = self.dataset
        long = ds.comp.long

        def options(col: str) -> list[str]:
            return sorted({str(v) for v in long[col] if str(v).strip()})

        return {
            "options": {f: options(f) for f in _FILTER_FIELDS},
            "labels": _FILTER_LABELS,
            "selected": {f: sorted(getattr(self.filters, f) or [])
                         for f in _FILTER_FIELDS},
            "search": self.filters.search,
            "completeOnly": self.filters.complete_competitors_only,
            "activeCount": self.filters.active_count,
            "describe": self.filters.describe(),
        }

    def _kpi(self, filtered, value_col, names, pay_cols) -> list[dict]:
        overall = metrics.overall_terms(filtered, value_col, pay_cols)
        comfy = _val(overall.get(PAY_COMFY))
        comfy_note = "взвешено по " + ("цене" if value_col == "price" else "продажам")
        if self.mapping.applies_to(self.bank):
            comfy_note += f" · маппинг {self.mapping.bank}"
        if self.apple.is_active:
            comfy_note += f" · {self.apple.brand}: переопределено"
        cards = [
            {"title": "SKU в выборке", "value": _fmt_count(len(filtered)),
             "sub": f"банк: {self.bank}", "color": None, "accent": False},
            {"title": "Comfy: средний срок",
             "value": f"{comfy:.2f}" if comfy is not None else "—",
             "sub": comfy_note, "color": "good", "accent": True},
        ]
        for domain, disp in names.items():
            avg = _val(overall.get(pay_col(domain)))
            sub, color = "", None
            if avg is not None and comfy is not None:
                delta = comfy - avg
                sign = "+" if delta >= 0 else "−"
                sub = f"Δ Comfy {sign}{abs(delta):.2f} платежей"
                color = "good" if delta >= 0 else "bad"
            cards.append({
                "title": f"{disp}: средний срок",
                "value": f"{avg:.2f}" if avg is not None else "—",
                "sub": sub, "color": color, "accent": False,
            })
        has_comp = filtered["comp_max_bank"].notna() & filtered["pay_comfy"].notna()
        if has_comp.any():
            share = float((filtered.loc[has_comp, "dev_bank"] >= 0).mean())
            cards.append({
                "title": "Comfy ≥ лучшего конкурента", "value": f"{share:.0%}",
                "sub": f"среди {_fmt_count(int(has_comp.sum()))} SKU с данными",
                "color": "good" if share >= 0.5 else "bad", "accent": False,
            })
        return cards

    def _charts(self, filtered, value_col, names, pay_cols) -> dict | None:
        if not len(filtered):
            return None
        terms = metrics.weighted_terms(filtered, value_col, pay_cols)
        base = filtered.groupby("category")[value_col].sum()
        top = base.sort_values(ascending=False).head(CHART_CATEGORIES_LIMIT).index
        terms = terms.loc[[c for c in terms.index if c in set(top)]]

        series_keys = [(PAY_COMFY, "Comfy")] + [(pay_col(d), n) for d, n in names.items()]
        series = []
        for i, (key, disp) in enumerate(series_keys):
            series.append({
                "name": disp,
                "color": brand.SERIES[i % len(brand.SERIES)],
                "values": [_val(terms.loc[cat, key]) if cat in terms.index else None
                           for cat in terms.index],
            })
        hint = ""
        if len(base) > CHART_CATEGORIES_LIMIT:
            hint = (f"Топ-{CHART_CATEGORIES_LIMIT} категорий по "
                    f"{'продажам' if value_col == 'sales' else 'стоимости'} "
                    f"из {len(base)}")

        dist = metrics.deviation_distribution(filtered)
        dev_items = [
            {"label": f"{int(dev):+d}", "count": int(cnt),
             "kind": "bad" if dev < 0 else ("good" if dev > 0 else "zero")}
            for dev, cnt in dist.items()
        ]
        return {
            "terms": {"categories": [str(c) for c in terms.index],
                      "series": series, "hint": hint},
            "dev": {"items": dev_items},
        }

    def _table(self, filtered, value_col, names) -> dict:
        columns = [
            {"key": "sku", "label": "КодТовара", "type": "text"},
            {"key": "name", "label": "Товар", "type": "name"},
            {"key": "brand", "label": "Бренд", "type": "text"},
            {"key": "category", "label": "Категория", "type": "text"},
            {"key": "sales", "label": "Продажи", "type": "money"},
            {"key": "price", "label": "Цена", "type": "money"},
            {"key": "structure", "label": "Структура", "type": "pct"},
            {"key": PAY_COMFY, "label": "Comfy", "type": "pay"},
            *[{"key": pay_col(d), "label": disp, "type": "pay"}
              for d, disp in names.items()],
            {"key": "dev_bank", "label": "Откл.", "type": "dev"},
            {"key": "beacon_proposed", "label": "Предл. КМ", "type": "pay"},
        ]
        if not len(filtered):
            return {"columns": columns, "rows": [], "page": 0, "pages": 1,
                    "total": 0, "sort": self.sort_field, "asc": self.sort_asc}

        df = filtered.copy()
        df["structure"] = metrics.structure(df, value_col)

        def _sort_key(s: pd.Series) -> pd.Series:
            if s.name == "sku":
                num = pd.to_numeric(s, errors="coerce")
                if num.notna().all():
                    return num
            if not pd.api.types.is_numeric_dtype(s):
                return s.astype(str).str.lower()
            return s

        sort_field = self.sort_field if self.sort_field in df.columns else "sku"
        df = df.sort_values(sort_field, ascending=self.sort_asc,
                            na_position="last", key=_sort_key)
        pages = max(1, math.ceil(len(df) / PAGE_SIZE))
        self.page = min(self.page, pages - 1)
        start = self.page * PAGE_SIZE
        chunk = df.iloc[start:start + PAGE_SIZE]

        keys = [c["key"] for c in columns]
        rows = [{k: _val(rec.get(k)) for k in keys}
                for rec in chunk.to_dict("records")]
        return {
            "columns": columns, "rows": rows,
            "page": self.page, "pages": pages, "total": len(df),
            "totalLabel": _fmt_count(len(df)),
            "sort": self.sort_field, "asc": self.sort_asc,
        }

    def _mapping_state(self) -> dict:
        m = self.mapping
        badge = ""
        if m.is_active:
            badge = f"Маппинг Comfy → {m.bank}: изменено {m.changed_count}"
            if self.dataset is not None and m.bank not in self.dataset.banks:
                badge += " (банк не найден в файле)"
        return {
            "bank": m.bank,
            "banks": self.dataset.banks if self.dataset else [],
            "values": [{"max": k, "mapped": v}
                       for k, v in sorted(m.table.items())],
            "active": m.is_active,
            "badge": badge,
        }

    def _apple_state(self) -> dict:
        o = self.apple
        brands: list[str] = []
        if self.dataset is not None:
            brands = sorted({str(b) for b in self.dataset.comp.long["brand"]
                             if str(b).strip()})
        badge = ""
        if o.is_active:
            badge = f"{o.brand}: платежи заданы для {len(o.per_bank)} банк."
            if self.dataset is not None and not any(
                    b in self.dataset.banks for b in o.per_bank):
                badge += " (банки не найдены в файле)"
        return {
            "brand": o.brand,
            "brands": brands,
            "banks": self.dataset.banks if self.dataset else [],
            "perBank": dict(o.per_bank),
            "active": o.is_active,
            "badge": badge,
        }
