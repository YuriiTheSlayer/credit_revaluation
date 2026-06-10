"""Смоук-тест UI-логики без открытия окна: построение дашборда на фейковой
странице, загрузка данных, переключение банка/весов, фильтры, пагинация."""

import pytest

from core.model import ALL_BANKS, Dataset
from parsers.competitors import parse_competitors_csv
from parsers.sales import parse_sales
from ui.dashboard import Dashboard


class FakePage:
    """Минимум интерфейса ft.Page, который использует Dashboard."""

    def __init__(self):
        self.overlay = []
        self.controls = []
        self.title = ""
        self.padding = 0
        self.scroll = None
        self.theme_mode = None
        self.opened = []

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        pass

    def open(self, control):
        self.opened.append(control)

    def close(self, control):
        pass


@pytest.fixture
def dash(kniga_path, sales_path) -> Dashboard:
    page = FakePage()
    d = Dashboard(page)  # type: ignore[arg-type]
    dataset = Dataset(parse_competitors_csv(kniga_path)).attach_sales(parse_sales(sales_path))
    d.dataset = dataset
    d.bank = dataset.banks[0]
    d._after_dataset_change()
    return d


def test_dashboard_builds_kpi_table_charts(dash):
    assert dash.bank == "Monobank"
    assert dash.bank_selector.visible
    # сегменты банков: 3 из файла + «Все банки»
    values = [s.value for s in dash.bank_selector.segments]
    assert values == ["Monobank", "ПриватБанк", "ПУМБ", ALL_BANKS]
    assert len(dash.kpi_row.controls) >= 4          # SKU, Comfy, конкуренты…
    assert dash.table.visible and len(dash.table.rows) == 12
    assert dash.charts_tabs.visible


def test_filter_options_distinct_from_file(dash):
    assert "Thomas" in dash.f_brand.options
    assert "ДП техніка" in dash.f_business.options
    assert dash.f_category.options                  # категории подхвачены
    assert dash.f_assort.options and dash.f_assort_global.options


def test_bank_switch_changes_table(dash):
    class E:  # минимальный ControlEvent
        def __init__(self, control):
            self.control = control

    before = len(dash.table.rows)
    dash.bank_selector.selected = {"ПУМБ"}
    dash._bank_changed(E(dash.bank_selector))
    assert dash.bank == "ПУМБ"
    assert len(dash.table.rows) == before           # SKU те же, цифры другие


def test_filters_and_reset(dash):
    dash.f_brand.selected = {"Thomas"}
    dash._filters_changed()
    assert len(dash.table.rows) == 3                # 20671, 20676, 901657
    dash._reset_filters(None)
    assert len(dash.table.rows) == 12


def test_search_filters_rows(dash):
    dash.search_field.value = "ROMA"
    dash._filters_changed()
    assert 3 <= len(dash.table.rows) <= 4


def test_weight_switch_requires_sales(kniga_path):
    page = FakePage()
    d = Dashboard(page)  # type: ignore[arg-type]
    d.dataset = Dataset(parse_competitors_csv(kniga_path))   # без продаж
    d.bank = d.dataset.banks[0]
    d._after_dataset_change()
    assert d.weight_mode == "price"                 # принудительно «по стоимости»


def test_export_frames_logic(dash, tmp_path):
    out = tmp_path / "out.xlsx"
    dash.export_all_banks.value = True
    dash._do_export(str(out))
    import openpyxl
    wb = openpyxl.load_workbook(out)
    assert len(wb.sheetnames) == 2 * (len(dash.dataset.banks) + 1)


def test_mapping_applies_in_dashboard(dash):
    from core.mapping import ComfyMapping

    dash.mapping = ComfyMapping(bank="Monobank", table={10: 5, 7: 4, 18: 9})
    dash._wide_cache.clear()
    dash._sync_mapping_badge()
    assert dash.mapping_badge.visible
    assert "Monobank" in dash.mapping_badge.content.value

    wide = dash._get_wide("Monobank")
    row = wide[wide["sku"] == "20671"].iloc[0]
    assert row["pay_comfy"] == 5 and row["dev_bank"] == -1
    privat = dash._get_wide("ПриватБанк")
    assert privat[privat["sku"] == "20671"].iloc[0]["pay_comfy"] == 10

    dash._refresh()          # KPI/таблица пересчитываются без ошибок
    assert dash.table.rows


def test_mapping_dialog_builds(dash):
    dash._open_mapping_dialog(None)
    assert dash.page.opened, "диалог маппинга должен открыться"
    dialog = dash.page.opened[-1]
    assert "Маппинг" in dialog.title.value


def _walk(control):
    yield control
    for attr in ("controls", "content", "tabs"):
        value = getattr(control, attr, None)
        if value is None:
            continue
        children = value if isinstance(value, list) else [value]
        for child in children:
            if hasattr(child, "_get_control_name") or hasattr(child, "controls") \
                    or hasattr(child, "content"):
                yield from _walk(child)


def test_no_expand_inside_wrap_rows(dash):
    """Flutter Wrap не поддерживает expand: такой ребёнок раздувается в
    гигантский пустой блок и выталкивает контент за экран (регрессия макета)."""
    import flet as ft

    wrap_rows = [
        c for root in dash.page.controls for c in _walk(root)
        if isinstance(c, ft.Row) and getattr(c, "wrap", False)
    ]
    assert wrap_rows, "ожидали хотя бы один wrap-Row (фильтры, KPI)"
    for row in wrap_rows:
        for child in row.controls:
            assert not getattr(child, "expand", None), (
                f"expand внутри wrap-Row ломает layout: {child}"
            )
