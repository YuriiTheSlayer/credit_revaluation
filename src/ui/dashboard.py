"""Экран дашборда: компактная шапка (файлы + экспорт), тулбар банка/весов,
фильтры, KPI, таблица SKU и графики в карточках. Контент начинается сразу под
тулбаром; страница не скроллится «в пустоту» — прокручивается только область
контента. Тяжёлая работа (парсинг, экспорт) — в отдельном потоке.
"""

from __future__ import annotations

import math
import threading
import traceback
from pathlib import Path

import flet as ft
import pandas as pd

from core import brand, metrics
from core.mapping import ComfyMapping, guess_reduced_bank
from core.model import ALL_BANKS, Dataset, Filters, PAY_COMFY, pay_col
from export.excel import default_filename, export_report
from parsers.competitors import parse_competitors_csv
from parsers.sales import parse_sales
from ui import widgets as w


class Dashboard:
    PAGE_SIZE = 20
    CHART_CATEGORIES_LIMIT = 10

    def __init__(self, page: ft.Page):
        self.page = page
        page.title = f"{brand.APP_TITLE} — Comfy vs конкуренты"
        page.padding = 0
        page.spacing = 0
        page.scroll = None
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = w.build_theme()
        page.bgcolor = brand.BG

        # --- состояние -----------------------------------------------------
        self.dataset: Dataset | None = None
        self.bank: str | None = None
        self.weight_mode: str = "sales"          # 'sales' | 'price'
        self.filters = Filters()
        self.mapping = ComfyMapping.load()       # маппинг доступности Comfy
        self.sort_field: str = "sku"
        self.sort_asc: bool = True
        self.table_page: int = 0
        self._wide_cache: dict[str, pd.DataFrame] = {}   # банк → широкая таблица
        self._warnings: list[str] = []

        # --- диалоги выбора файлов ------------------------------------------
        self.pick_competitors = ft.FilePicker(on_result=self._competitors_picked)
        self.pick_sales = ft.FilePicker(on_result=self._sales_picked)
        self.pick_save = ft.FilePicker(on_result=self._save_target_picked)
        page.overlay.extend([self.pick_competitors, self.pick_sales, self.pick_save])

        # --- шапка: бренд + файлы + экспорт ---------------------------------
        self.btn_competitors = ft.FilledTonalButton(
            "Конкуренты (CSV)", icon=ft.Icons.UPLOAD_FILE,
            on_click=lambda _e: self.pick_competitors.pick_files(
                allow_multiple=False, allowed_extensions=["csv", "txt"]),
        )
        self.btn_sales = ft.FilledTonalButton(
            "Продажи (CSV/XLSX)", icon=ft.Icons.SHOPPING_CART_OUTLINED,
            on_click=lambda _e: self.pick_sales.pick_files(
                allow_multiple=False,
                allowed_extensions=["csv", "txt", "xlsx", "xlsm"]),
        )
        self.status_competitors = ft.Text(
            "CSV конкурентов: не загружен", size=11, color=w.MUTED)
        self.status_sales = ft.Text(
            "Файл продаж: не загружен (опционально)", size=11, color=w.MUTED)
        self.warnings_btn = ft.IconButton(
            icon=ft.Icons.WARNING_AMBER_ROUNDED, icon_color=brand.ORANGE,
            visible=False, tooltip="Предупреждения", on_click=self._show_warnings,
        )
        self.export_all_banks = ft.Checkbox(label="Все банки", value=False)
        self.export_btn = ft.FilledButton(
            "Экспорт в Excel", icon=ft.Icons.DOWNLOAD,
            on_click=self._export_clicked, disabled=True,
        )
        header = ft.Container(
            bgcolor=brand.SURFACE,
            padding=ft.padding.symmetric(10, 16),
            border=ft.border.only(bottom=ft.BorderSide(1, brand.OUTLINE)),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            w.brand_header(),
                            ft.Container(expand=True),
                            self.warnings_btn,
                            self.btn_competitors,
                            self.btn_sales,
                            ft.Container(width=10),
                            self.export_all_banks,
                            self.export_btn,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row([self.status_competitors, self.status_sales], spacing=24),
                ],
                spacing=6,
                tight=True,
            ),
        )

        self.progress = ft.ProgressBar(visible=False, color=brand.GREEN,
                                       bgcolor=brand.GREEN_TINT)

        # --- тулбар: банк, веса, маппинг ------------------------------------
        self.bank_selector = ft.SegmentedButton(
            segments=[ft.Segment(value="-", label=ft.Text("Загрузите CSV"))],
            selected={"-"}, on_change=self._bank_changed, visible=False,
        )
        self.weight_selector = ft.SegmentedButton(
            segments=[
                ft.Segment(value="sales", label=ft.Text("Веса: продажи")),
                ft.Segment(value="price", label=ft.Text("Веса: стоимость")),
            ],
            selected={"sales"}, on_change=self._weight_changed, visible=False,
        )
        self.mapping_btn = ft.OutlinedButton(
            "Маппинг Comfy", icon=ft.Icons.TUNE,
            on_click=self._open_mapping_dialog, disabled=True,
            tooltip="Доступность Comfy в банке со сниженными условиями (Monobank)",
        )
        self.mapping_badge = ft.Container(
            content=ft.Text("", size=11, weight=ft.FontWeight.W_600,
                            color=brand.ORANGE),
            bgcolor=brand.ORANGE_TINT, padding=ft.padding.symmetric(4, 8),
            border_radius=6, visible=False,
        )
        toolbar = ft.Container(
            padding=ft.padding.only(left=16, right=16, top=10, bottom=2),
            content=ft.Row(
                [self.bank_selector, self.weight_selector,
                 self.mapping_btn, self.mapping_badge],
                spacing=10, scroll=ft.ScrollMode.AUTO,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        # --- фильтры ---------------------------------------------------------
        self.f_business = w.MultiSelect(page, "Бизнес", self._filters_changed)
        self.f_category = w.MultiSelect(page, "Категория", self._filters_changed)
        self.f_subcategory = w.MultiSelect(page, "Подкатегория", self._filters_changed)
        self.f_assort = w.MultiSelect(page, "Тип ассортимента", self._filters_changed)
        self.f_assort_global = w.MultiSelect(page, "ТипАссортиментаОбщий",
                                             self._filters_changed)
        self.f_brand = w.MultiSelect(page, "Бренд", self._filters_changed)
        self.search_field = ft.TextField(
            hint_text="Поиск: название или код товара…",
            prefix_icon=ft.Icons.SEARCH, dense=True, width=280,
            on_change=self._search_changed, disabled=True,
        )
        self.reset_filters_btn = ft.TextButton(
            "Сбросить", icon=ft.Icons.FILTER_ALT_OFF,
            on_click=self._reset_filters, disabled=True,
        )
        filters_bar = ft.Container(
            padding=ft.padding.only(left=16, right=16, top=6, bottom=4),
            content=ft.Row(
                [self.f_business.control, self.f_category.control,
                 self.f_subcategory.control, self.f_assort.control,
                 self.f_assort_global.control, self.f_brand.control,
                 self.search_field, self.reset_filters_btn],
                wrap=True, spacing=8, run_spacing=8,
            ),
        )

        # --- контент: KPI, таблица, графики ----------------------------------
        self.empty_hint = ft.Container(
            ft.Column(
                [
                    ft.Icon(ft.Icons.UPLOAD_FILE, size=44, color=w.MUTED),
                    ft.Text("Загрузите CSV-выгрузку по конкурентам, "
                            "чтобы построить дашборд", color=w.MUTED),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.alignment.center, padding=48,
        )
        self.kpi_row = ft.Row(wrap=True, spacing=10, run_spacing=10)

        self.table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("—"))], rows=[],
            heading_row_color=brand.GREEN_TINT, column_spacing=16,
            data_row_min_height=32, data_row_max_height=38, visible=False,
        )
        self.table_caption = ft.Text("Таблица SKU", size=13,
                                     weight=ft.FontWeight.W_700,
                                     color=brand.GRAPHITE)
        self.table_pager = ft.Row(
            [
                ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=self._prev_page,
                              icon_size=18),
                ft.Text("", size=12, color=w.MUTED),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=self._next_page,
                              icon_size=18),
            ],
            spacing=2, visible=False,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.table_card = w.section_card(
            ft.Row([self.table_caption, ft.Container(expand=True),
                    self.table_pager],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([self.table], scroll=ft.ScrollMode.AUTO),
            visible=False,
        )

        self.chart_terms_host = ft.Container(padding=8)
        self.chart_dev_host = ft.Container(padding=8)
        self.charts_tabs = ft.Tabs(
            tabs=[
                ft.Tab(text="Средний срок по категориям",
                       content=self.chart_terms_host),
                ft.Tab(text="Распределение отклонений",
                       content=self.chart_dev_host),
            ],
            height=380, visible=False,
        )
        self.charts_card = w.section_card(self.charts_tabs, visible=False)

        content = ft.Container(
            expand=True,
            padding=ft.padding.symmetric(12, 16),
            content=ft.Column(
                [self.empty_hint, self.kpi_row, self.table_card, self.charts_card],
                spacing=12, scroll=ft.ScrollMode.AUTO, expand=True,
            ),
        )

        page.add(header, self.progress, toolbar, filters_bar, content)

    # ------------------------------------------------------------------ utils
    def _run_bg(self, fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _busy(self, value: bool) -> None:
        self.progress.visible = value
        if self.progress.page:
            self.progress.update()

    def _toast(self, message: str, error: bool = False) -> None:
        self.page.open(ft.SnackBar(
            ft.Text(message, color=brand.RED if error else None),
            bgcolor=brand.RED_TINT if error else None,
        ))

    def _error_dialog(self, title: str, message: str) -> None:
        dlg = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Text(message, selectable=True),
            actions=[ft.TextButton("Понятно", on_click=lambda e: self.page.close(dlg))],
        )
        self.page.open(dlg)

    def _show_warnings(self, _e) -> None:
        dlg = ft.AlertDialog(
            title=ft.Text(f"Предупреждения ({len(self._warnings)})"),
            content=ft.Container(
                ft.Column([ft.Text(f"• {msg}", size=12) for msg in self._warnings],
                          scroll=ft.ScrollMode.AUTO, tight=True, spacing=6),
                width=520, height=min(420, 60 + 30 * len(self._warnings)),
            ),
            actions=[ft.TextButton("Закрыть", on_click=lambda e: self.page.close(dlg))],
        )
        self.page.open(dlg)

    @staticmethod
    def _fmt_count(n: int) -> str:
        return f"{n:,}".replace(",", " ")

    # ------------------------------------------------------------ загрузка CSV
    def _competitors_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.files:
            self._busy(True)
            self._run_bg(self._load_competitors, e.files[0].path, e.files[0].name)

    def _load_competitors(self, path: str, name: str) -> None:
        try:
            data = parse_competitors_csv(path, source_name=name)
            dataset = Dataset(data)
            if self.dataset is not None and self.dataset.sales is not None:
                dataset = dataset.attach_sales(self.dataset.sales)
            self.dataset = dataset
            self.bank = data.banks[0] if data.banks else None
            self.table_page = 0
            self._after_dataset_change()
            self.status_competitors.value = (
                f"Конкуренты: {name} · {data.encoding} · "
                f"SKU {self._fmt_count(data.sku_count)} · банков {len(data.banks)} · "
                f"конкурентов {len(data.competitors)}"
            )
            self.status_competitors.color = w.GOOD
        except Exception as exc:  # noqa: BLE001 — показываем пользователю
            traceback.print_exc()
            self._error_dialog("Не удалось загрузить CSV конкурентов", str(exc))
        finally:
            self._busy(False)
            self.page.update()

    def _sales_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.files:
            self._busy(True)
            self._run_bg(self._load_sales, e.files[0].path, e.files[0].name)

    def _load_sales(self, path: str, name: str) -> None:
        try:
            sales = parse_sales(path, source_name=name)
            self.status_sales.value = (
                f"Продажи: {name} · строк {self._fmt_count(len(sales.df))} · "
                f"оборот {w.fmt_money(sales.total)}"
            )
            self.status_sales.color = w.GOOD
            if self.dataset is not None:
                self.dataset = self.dataset.attach_sales(sales)
                self._after_dataset_change()
            else:
                # продажи загрузили раньше конкурентов — подключим позже
                self._pending_sales = sales
                self._toast("Продажи загружены. Теперь загрузите CSV конкурентов.")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error_dialog("Не удалось загрузить файл продаж", str(exc))
        finally:
            self._busy(False)
            self.page.update()

    def _after_dataset_change(self) -> None:
        """Дозагрузка отложенных продаж, перестройка банков/фильтров, пересчёт."""
        ds = self.dataset
        if ds is None:
            return
        pending = getattr(self, "_pending_sales", None)
        if pending is not None and ds.sales is None:
            self.dataset = ds = ds.attach_sales(pending)
            self._pending_sales = None

        self.bank_selector.segments = [
            ft.Segment(value=b, label=ft.Text(b)) for b in ds.banks
        ] + [ft.Segment(value=ALL_BANKS, label=ft.Text(ALL_BANKS))]
        if self.bank not in ds.banks + [ALL_BANKS]:
            self.bank = ds.banks[0] if ds.banks else ALL_BANKS
        self.bank_selector.selected = {self.bank}
        self.bank_selector.visible = True
        self.weight_selector.visible = True
        if not ds.has_sales:
            self.weight_mode = "price"
            self.weight_selector.selected = {"price"}

        long = ds.comp.long

        def options(col: str) -> list[str]:
            return sorted({str(v) for v in long[col] if str(v).strip()})

        self.f_business.set_options(options("business"))
        self.f_category.set_options(options("category"))
        self.f_subcategory.set_options(options("subcategory"))
        self.f_assort.set_options(options("assort_type"))
        self.f_assort_global.set_options(options("assort_type_global"))
        self.f_brand.set_options(options("brand"))
        self.search_field.disabled = False
        self.reset_filters_btn.disabled = False
        self.export_btn.disabled = False
        self.mapping_btn.disabled = False
        self.empty_hint.visible = False

        # маппинг Comfy: угадываем банк со сниженной доступностью, подтягиваем
        # новые значения «макс. платежей» из файла
        if self.mapping.bank is None:
            self.mapping.bank = guess_reduced_bank(ds.banks)
        comfy_values = sorted(int(v) for v in long["comfy_max"].dropna().unique())
        self.mapping.sync_values(comfy_values)
        self._sync_mapping_badge()

        self._warnings = list(ds.comp.warnings) + list(ds.sales_warnings)
        self.warnings_btn.visible = bool(self._warnings)
        self.warnings_btn.tooltip = f"Предупреждения: {len(self._warnings)}"

        self._wide_cache.clear()
        self._refresh()

    # ----------------------------------------------------------------- события
    def _bank_changed(self, e: ft.ControlEvent) -> None:
        selected = next(iter(e.control.selected), None)
        if selected and selected != self.bank:
            self.bank = selected
            self.table_page = 0
            self._refresh()
            self.page.update()

    def _weight_changed(self, e: ft.ControlEvent) -> None:
        mode = next(iter(e.control.selected), "sales")
        if mode == "sales" and (self.dataset is None or not self.dataset.has_sales):
            self.weight_selector.selected = {"price"}
            if self.weight_selector.page:
                self.weight_selector.update()
            self._toast("Сначала загрузите файл продаж — веса по продажам недоступны.",
                        error=True)
            return
        self.weight_mode = mode
        self._refresh()
        self.page.update()

    def _filters_changed(self) -> None:
        self.filters = Filters(
            business=self.f_business.value,
            category=self.f_category.value,
            subcategory=self.f_subcategory.value,
            assort_type=self.f_assort.value,
            assort_type_global=self.f_assort_global.value,
            brand=self.f_brand.value,
            search=self.search_field.value or "",
        )
        self.table_page = 0
        self._refresh()
        self.page.update()

    def _search_changed(self, _e) -> None:
        self._filters_changed()

    def _reset_filters(self, _e) -> None:
        for f in (self.f_business, self.f_category, self.f_subcategory,
                  self.f_assort, self.f_assort_global, self.f_brand):
            f.reset()
        self.search_field.value = ""
        self._filters_changed()

    def _prev_page(self, _e) -> None:
        if self.table_page > 0:
            self.table_page -= 1
            self._refresh()
            self.page.update()

    def _next_page(self, _e) -> None:
        self.table_page += 1
        self._refresh()
        self.page.update()

    def _sort_clicked(self, field: str):
        def handler(e: ft.DataColumnSortEvent) -> None:
            self.sort_asc = not self.sort_asc if self.sort_field == field else True
            self.sort_field = field
            self.table_page = 0
            self._refresh()
            self.page.update()
        return handler

    # ----------------------------------------------------------- маппинг Comfy
    def _sync_mapping_badge(self) -> None:
        m = self.mapping
        if m.is_active:
            note = f"Маппинг Comfy → {m.bank}: изменено {m.changed_count}"
            if self.dataset is not None and m.bank not in self.dataset.banks:
                note += " (банк не найден в файле)"
            self.mapping_badge.content.value = note
            self.mapping_badge.visible = True
        else:
            self.mapping_badge.visible = False

    def _open_mapping_dialog(self, _e) -> None:
        if self.dataset is None:
            self._toast("Сначала загрузите CSV конкурентов.", error=True)
            return
        ds = self.dataset
        values = sorted(int(v) for v in ds.comp.long["comfy_max"].dropna().unique())
        self.mapping.sync_values(values)

        bank_dd = ft.Dropdown(
            label="Банк со сниженной доступностью",
            value=self.mapping.bank or guess_reduced_bank(ds.banks) or ds.banks[0],
            options=[ft.dropdown.Option(b) for b in ds.banks],
            width=300, dense=True,
        )
        fields: dict[int, ft.TextField] = {
            v: ft.TextField(
                value=str(self.mapping.table.get(v, v)), width=90, dense=True,
                text_align=ft.TextAlign.RIGHT,
                keyboard_type=ft.KeyboardType.NUMBER,
            )
            for v in values
        }
        rows = [
            ft.Row(
                [ft.Text(f"Макс. {v} платежей", width=150, size=13),
                 ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=w.MUTED),
                 fields[v]],
                spacing=8,
            )
            for v in values
        ]
        hint = ft.Text(
            "«Comfy. MAX платежей» в выгрузке — максимальная доступность "
            "(уровень ПриватБанк/ПУМБ). Укажите, какая доступность Comfy "
            "действует в выбранном банке: расчёты и экспорт в разрезе этого "
            "банка будут использовать приведённые значения.",
            size=12, color=w.MUTED,
        )

        def reset(_e) -> None:
            for v, f in fields.items():
                f.value = str(v)
                f.update()

        def save(_e) -> None:
            try:
                table = {v: int(str(f.value).strip()) for v, f in fields.items()}
            except ValueError:
                self._toast("Все значения маппинга должны быть целыми числами.",
                            error=True)
                return
            if any(x < 0 for x in table.values()):
                self._toast("Количество платежей не может быть отрицательным.",
                            error=True)
                return
            self.mapping.bank = bank_dd.value
            self.mapping.table = table
            try:
                self.mapping.save()
            except OSError as exc:
                self._toast(f"Маппинг применён, но не сохранён на диск: {exc}",
                            error=True)
            self._wide_cache.clear()
            self._sync_mapping_badge()
            self.page.close(dialog)
            self._refresh()
            self.page.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Маппинг доступности Comfy"),
            content=ft.Container(
                ft.Column([hint, bank_dd, ft.Divider(), *rows],
                          scroll=ft.ScrollMode.AUTO, tight=True, spacing=10),
                width=420, height=min(520, 220 + 46 * len(rows)),
            ),
            actions=[
                ft.TextButton("Сбросить (1:1)", on_click=reset),
                ft.TextButton("Отмена", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("Сохранить", on_click=save),
            ],
        )
        self.page.open(dialog)

    # ------------------------------------------------------------------ расчёт
    def _get_wide(self, bank: str) -> pd.DataFrame:
        """Широкая таблица банка с кэшем (инвалидация — при смене данных/маппинга)."""
        assert self.dataset is not None
        if bank not in self._wide_cache:
            self._wide_cache[bank] = self.dataset.wide(bank, self.mapping)
        return self._wide_cache[bank]

    def _current_frames(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(широкая таблица банка, отфильтрованная)."""
        assert self.bank is not None
        wide = self._get_wide(self.bank)
        return wide, self.filters.apply(wide)

    def _refresh(self) -> None:
        if self.dataset is None or self.bank is None:
            return
        ds = self.dataset
        _, filtered = self._current_frames()
        value_col = "sales" if self.weight_mode == "sales" else "price"
        names = ds.competitor_names()
        pay_cols = [PAY_COMFY] + [pay_col(d) for d in names]

        self._refresh_kpi(filtered, value_col, names, pay_cols)
        self._refresh_charts(filtered, value_col, names, pay_cols)
        self._refresh_table(filtered, value_col, names)

    def _refresh_kpi(self, filtered, value_col, names, pay_cols) -> None:
        overall = metrics.overall_terms(filtered, value_col, pay_cols)
        comfy = overall.get(PAY_COMFY, float("nan"))
        comfy_note = "взвешено по " + ("продажам", "цене")[value_col == "price"]
        if self.mapping.applies_to(self.bank):
            comfy_note += f" · маппинг {self.mapping.bank}"
        cards = [
            w.kpi_card("SKU в выборке", self._fmt_count(len(filtered)),
                       f"банк: {self.bank} · {self.filters.describe()}"),
            w.kpi_card("Comfy: средний срок", w.fmt_num(comfy), comfy_note,
                       value_color=w.GOOD, accent=True),
        ]
        for domain, disp in names.items():
            avg = overall.get(pay_col(domain), float("nan"))
            delta = (comfy - avg) if not (pd.isna(comfy) or pd.isna(avg)) else float("nan")
            sub, color = "", None
            if not pd.isna(delta):
                sign = "+" if delta >= 0 else "−"
                sub = f"Δ Comfy {sign}{abs(delta):.2f} платежей"
                color = w.GOOD if delta >= 0 else w.BAD
            cards.append(w.kpi_card(f"{disp}: средний срок", w.fmt_num(avg), sub,
                                    value_color=color))
        has_comp = filtered["comp_max_bank"].notna() & filtered["pay_comfy"].notna()
        if has_comp.any():
            share = float((filtered.loc[has_comp, "dev_bank"] >= 0).mean())
            cards.append(w.kpi_card(
                "Comfy ≥ лучшего конкурента", f"{share:.0%}",
                f"среди {self._fmt_count(int(has_comp.sum()))} SKU с данными конкурентов",
                value_color=w.GOOD if share >= 0.5 else w.BAD,
            ))
        self.kpi_row.controls = cards

    def _refresh_charts(self, filtered, value_col, names, pay_cols) -> None:
        visible = len(filtered) > 0
        self.charts_tabs.visible = visible
        self.charts_card.visible = visible
        if not visible:
            return
        terms = metrics.weighted_terms(filtered, value_col, pay_cols)
        # топ категорий по базе весов, чтобы график оставался читаемым
        base = filtered.groupby("category")[value_col].sum()
        top = base.sort_values(ascending=False).head(self.CHART_CATEGORIES_LIMIT).index
        terms = terms.loc[[c for c in terms.index if c in set(top)]]

        series = [(PAY_COMFY, "Comfy")] + [(pay_col(d), disp) for d, disp in names.items()]
        colors = {key: w.SERIES_COLORS[i % len(w.SERIES_COLORS)]
                  for i, (key, _) in enumerate(series)}

        groups, labels = [], []
        for gi, (cat, row) in enumerate(terms.iterrows()):
            rods = []
            for key, disp in series:
                val = row.get(key)
                if pd.isna(val):
                    continue
                rods.append(ft.BarChartRod(
                    from_y=0, to_y=round(float(val), 2), width=14,
                    color=colors[key], tooltip=f"{disp}: {val:.2f}", border_radius=2,
                ))
            groups.append(ft.BarChartGroup(x=gi, bar_rods=rods, bars_space=3))
            labels.append(ft.ChartAxisLabel(
                value=gi,
                label=ft.Container(
                    ft.Text(str(cat)[:18], size=10), padding=ft.padding.only(top=4)),
            ))
        chart = ft.BarChart(
            bar_groups=groups,
            bottom_axis=ft.ChartAxis(labels=labels, labels_size=36),
            left_axis=ft.ChartAxis(labels_size=34, title=ft.Text("платежей", size=10)),
            horizontal_grid_lines=ft.ChartGridLines(interval=3,
                                                    color=brand.OUTLINE),
            groups_space=26, expand=True,
        )
        hint = ""
        if len(base) > self.CHART_CATEGORIES_LIMIT:
            hint = (f"Топ-{self.CHART_CATEGORIES_LIMIT} категорий по "
                    f"{'продажам' if value_col == 'sales' else 'стоимости'} "
                    f"из {len(base)}; уточните выборку фильтрами.")
        self.chart_terms_host.content = ft.Column([
            ft.Row([
                w.legend([(disp, colors[key]) for key, disp in series]),
                ft.Container(expand=True),
                ft.Text(hint, size=11, color=w.MUTED),
            ]),
            ft.Container(chart, height=280),
        ])

        dist = metrics.deviation_distribution(filtered)
        if len(dist):
            dgroups, dlabels = [], []
            for gi, (dev, count) in enumerate(dist.items()):
                color = w.BAD if dev < 0 else (w.GOOD if dev > 0 else w.MUTED)
                dgroups.append(ft.BarChartGroup(x=gi, bar_rods=[ft.BarChartRod(
                    from_y=0, to_y=int(count), width=18, color=color,
                    tooltip=f"Откл. {dev:+.0f}: {count} SKU", border_radius=2,
                )]))
                dlabels.append(ft.ChartAxisLabel(
                    value=gi, label=ft.Container(ft.Text(f"{dev:+.0f}", size=10),
                                                 padding=ft.padding.only(top=4))))
            self.chart_dev_host.content = ft.Column([
                ft.Text("Откл. = платежи Comfy − лучший конкурент (по выбранному банку)",
                        size=11, color=w.MUTED),
                ft.Container(ft.BarChart(
                    bar_groups=dgroups,
                    bottom_axis=ft.ChartAxis(labels=dlabels, labels_size=30),
                    left_axis=ft.ChartAxis(labels_size=34, title=ft.Text("SKU", size=10)),
                    horizontal_grid_lines=ft.ChartGridLines(
                        interval=1, color=brand.OUTLINE),
                    expand=True,
                ), height=280),
            ])
        else:
            self.chart_dev_host.content = ft.Text("Нет данных по конкурентам",
                                                  color=w.MUTED)

    def _refresh_table(self, filtered, value_col, names) -> None:
        visible = len(filtered) > 0
        self.table.visible = visible
        self.table_card.visible = visible
        self.table_pager.visible = len(filtered) > self.PAGE_SIZE
        if not visible:
            return

        df = filtered.copy()
        df["__structure__"] = metrics.structure(df, value_col)

        columns: list[tuple[str, str, bool]] = [   # (поле, заголовок, numeric)
            ("sku", "КодТовара", False),
            ("name", "Товар", False),
            ("brand", "Бренд", False),
            ("category", "Категория", False),
            ("sales", "Продажи", True),
            ("price", "Цена", True),
            ("__structure__", "Структура", True),
            (PAY_COMFY, "Comfy", True),
        ]
        columns += [(pay_col(d), disp, True) for d, disp in names.items()]
        columns += [("dev_bank", "Откл.", True)]

        def _sort_key(s: pd.Series) -> pd.Series:
            if s.name == "sku":
                num = pd.to_numeric(s, errors="coerce")
                if num.notna().all():
                    return num
            if not pd.api.types.is_numeric_dtype(s):
                return s.astype(str).str.lower()
            return s

        df = df.sort_values(
            self.sort_field, ascending=self.sort_asc, na_position="last",
            key=_sort_key,
        )
        pages = max(1, math.ceil(len(df) / self.PAGE_SIZE))
        self.table_page = min(self.table_page, pages - 1)
        start = self.table_page * self.PAGE_SIZE
        chunk = df.iloc[start:start + self.PAGE_SIZE]

        self.table.columns = [
            ft.DataColumn(ft.Text(title, weight=ft.FontWeight.W_600, size=12),
                          numeric=numeric, on_sort=self._sort_clicked(field))
            for field, title, numeric in columns
        ]
        sort_idx = next((i for i, c in enumerate(columns) if c[0] == self.sort_field), None)
        self.table.sort_column_index = sort_idx
        self.table.sort_ascending = self.sort_asc

        rows = []
        for _, rec in chunk.iterrows():
            cells = []
            for field, _title, _numeric in columns:
                val = rec[field]
                if field == "dev_bank":
                    if pd.isna(val):
                        cells.append(ft.DataCell(ft.Text("—", color=w.MUTED)))
                    else:
                        color = w.BAD if val < 0 else (w.GOOD if val > 0 else None)
                        cells.append(ft.DataCell(
                            ft.Text(f"{val:+.0f}", color=color,
                                    weight=ft.FontWeight.W_600)))
                elif field == "__structure__":
                    cells.append(ft.DataCell(ft.Text(f"{val:.2%}")))
                elif field in ("sales", "price"):
                    cells.append(ft.DataCell(ft.Text(w.fmt_money(val))))
                elif field == PAY_COMFY or field.startswith("pay::"):
                    cells.append(ft.DataCell(ft.Text(
                        "—" if pd.isna(val) else f"{val:.0f}",
                        color=w.MUTED if pd.isna(val) else None)))
                else:
                    text = "" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val)
                    cells.append(ft.DataCell(ft.Text(text, size=12)))
            rows.append(ft.DataRow(cells=cells))
        self.table.rows = rows

        self.table_caption.value = (
            f"Таблица SKU · {self._fmt_count(len(df))} строк"
        )
        self.table_pager.controls[1].value = (
            f"{start + 1}–{min(start + self.PAGE_SIZE, len(df))} из "
            f"{self._fmt_count(len(df))} · стр. {self.table_page + 1}/{pages}"
        )

    # ------------------------------------------------------------------ экспорт
    def _export_clicked(self, _e) -> None:
        if self.dataset is None or self.bank is None:
            self._toast("Сначала загрузите CSV конкурентов.", error=True)
            return
        if not self.dataset.has_sales:
            self._toast("Файл продаж не загружен — в экспорте будут только "
                        "веса по стоимости.")
        bank_slug = "all_banks" if self.export_all_banks.value else self.bank
        self.pick_save.save_file(
            dialog_title="Сохранить отчёт",
            file_name=default_filename(bank_slug),
            allowed_extensions=["xlsx"],
        )

    def _save_target_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.path:
            self._busy(True)
            self._run_bg(self._do_export, e.path)

    def _do_export(self, path: str) -> None:
        try:
            ds = self.dataset
            assert ds is not None
            banks = ds.banks + [ALL_BANKS] if self.export_all_banks.value else [self.bank]
            frames = [(b, self.filters.apply(self._get_wide(b))) for b in banks]
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            export_report(
                path, frames, ds.competitor_names(),
                has_sales=ds.has_sales,
                filters_desc=f"{self.filters.describe()}",
                mapping=self.mapping,
            )
            self._toast(f"Отчёт сохранён: {Path(path).name}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error_dialog("Ошибка экспорта", str(exc))
        finally:
            self._busy(False)
            self.page.update()


def mount(page: ft.Page) -> None:
    Dashboard(page)
