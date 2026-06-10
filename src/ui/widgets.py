"""Переиспользуемые элементы интерфейса в корпоративном стиле Comfy:
тема приложения, шапка-«логотип», KPI-карточки, мультивыбор, зоны загрузки.
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from core import brand

ACCENT = brand.GREEN
GOOD = brand.GREEN_DARK
BAD = brand.RED
MUTED = brand.MUTED

#: палитра серий для графика «ритейлеры» (Comfy — фирменный зелёный, первым)
SERIES_COLORS = brand.SERIES


def build_theme() -> ft.Theme:
    """Тема Material 3 на фирменном зелёном Comfy."""
    return ft.Theme(
        color_scheme_seed=brand.GREEN,
        color_scheme=ft.ColorScheme(
            primary=brand.GREEN,
            on_primary="#FFFFFF",
            primary_container=brand.GREEN_TINT,
            on_primary_container=brand.GREEN_DARK,
            secondary=brand.ORANGE,
            surface=brand.SURFACE,
        ),
        use_material3=True,
    )


def brand_header() -> ft.Row:
    """Словесный знак COMFY + название продукта (для шапки приложения)."""
    wordmark = ft.Container(
        ft.Text("COMFY", size=15, weight=ft.FontWeight.W_800, color="#FFFFFF"),
        bgcolor=brand.GREEN,
        padding=ft.padding.symmetric(6, 13),
        border_radius=8,
    )
    return ft.Row(
        [
            wordmark,
            ft.Column(
                [
                    ft.Text(brand.APP_TITLE, size=14,
                            weight=ft.FontWeight.W_700, color=brand.GRAPHITE),
                    ft.Text(brand.APP_SUBTITLE, size=11, color=MUTED),
                ],
                spacing=0, tight=True,
            ),
        ],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def fmt_num(value, digits: int = 2) -> str:
    """Число для UI: 16.41 → «16.41», NaN → «—»."""
    try:
        import math
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "—"
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def fmt_money(value) -> str:
    """Сумма с разделителями тысяч: 125000.5 → «125 001»."""
    try:
        import math
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "—"
        return f"{float(value):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def kpi_card(title: str, value: str, subtitle: str = "",
             value_color: str | None = None, accent: bool = False) -> ft.Container:
    """KPI-карточка; ``accent=True`` — фирменная зелёная подложка (для Comfy)."""
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(title, size=12, color=MUTED, weight=ft.FontWeight.W_500),
                ft.Text(value, size=24, weight=ft.FontWeight.BOLD,
                        color=value_color or brand.GRAPHITE),
                ft.Text(subtitle, size=11, color=MUTED),
            ],
            spacing=2,
            tight=True,
        ),
        padding=ft.padding.symmetric(12, 16),
        border=ft.border.all(1, brand.GREEN if accent else brand.OUTLINE),
        border_radius=12,
        bgcolor=brand.GREEN_TINT if accent else brand.SURFACE,
    )


def section_card(*controls: ft.Control, visible: bool = True,
                 padding: int = 12) -> ft.Container:
    """Белая карточка-секция: рамка, скругление — единый вид блоков контента."""
    return ft.Container(
        content=ft.Column(list(controls), spacing=8, tight=True),
        bgcolor=brand.SURFACE,
        border=ft.border.all(1, brand.OUTLINE),
        border_radius=12,
        padding=padding,
        visible=visible,
    )


class MultiSelect:
    """Фильтр-мультивыбор: кнопка со счётчиком + диалог с чекбоксами и поиском."""

    def __init__(self, page: ft.Page, label: str, on_change: Callable[[], None]):
        self.page = page
        self.label = label
        self.on_change = on_change
        self.options: list[str] = []
        self.selected: set[str] = set()
        self.button = ft.OutlinedButton(
            text=self._caption(),
            icon=ft.Icons.ARROW_DROP_DOWN,
            on_click=self._open,
            disabled=True,
        )

    @property
    def control(self) -> ft.Control:
        return self.button

    @property
    def value(self) -> set[str] | None:
        """Выбранные значения; None — фильтр не активен (выбрано всё/ничего)."""
        if not self.selected or self.selected == set(self.options):
            return None
        return set(self.selected)

    def set_options(self, options: list[str]) -> None:
        self.options = [o for o in options if str(o).strip()]
        self.selected &= set(self.options)
        self.button.disabled = not self.options
        self._sync_button()

    def reset(self) -> None:
        self.selected = set()
        self._sync_button()

    def _caption(self) -> str:
        if self.value is None:
            return f"{self.label}: все"
        return f"{self.label}: {len(self.selected)}"

    def _sync_button(self) -> None:
        self.button.text = self._caption()
        if self.button.page:
            self.button.update()

    def _open(self, _e) -> None:
        checks = [
            ft.Checkbox(label=str(opt), value=opt in self.selected,
                        data=opt, on_change=self._toggle)
            for opt in self.options
        ]
        listing = ft.Column(checks, scroll=ft.ScrollMode.AUTO, tight=True, spacing=0)
        search = ft.TextField(
            hint_text="Поиск значения…", dense=True, prefix_icon=ft.Icons.SEARCH,
            on_change=lambda e: self._filter_checks(checks, listing, e.control.value),
        )
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(self.label),
            content=ft.Container(
                ft.Column([search, listing], tight=True, spacing=8),
                width=380, height=420,
            ),
            actions=[
                ft.TextButton("Сбросить", on_click=lambda e: self._clear(checks)),
                ft.FilledButton("Готово", on_click=lambda e: self._close(dialog)),
            ],
        )
        self.page.open(dialog)

    def _filter_checks(self, checks, listing, query: str) -> None:
        q = (query or "").strip().lower()
        listing.controls = [c for c in checks if q in c.label.lower()] if q else checks
        listing.update()

    def _toggle(self, e: ft.ControlEvent) -> None:
        if e.control.value:
            self.selected.add(e.control.data)
        else:
            self.selected.discard(e.control.data)

    def _clear(self, checks) -> None:
        self.selected = set()
        for c in checks:
            c.value = False
            c.update()

    def _close(self, dialog) -> None:
        self.page.close(dialog)
        self._sync_button()
        self.on_change()


def legend(items: list[tuple[str, str]]) -> ft.Row:
    """Легенда графика: [(имя, цвет)] → ряд цветных чипов."""
    chips = [
        ft.Row(
            [
                ft.Container(width=12, height=12, bgcolor=color, border_radius=3),
                ft.Text(name, size=12),
            ],
            spacing=4,
            tight=True,
        )
        for name, color in items
    ]
    return ft.Row(chips, spacing=14, wrap=True)
