"""Тесты маппинга доступности Comfy (макс. доступность → доступность в банке)."""

import pandas as pd
import pytest

from core.mapping import ComfyMapping, guess_reduced_bank
from core.model import ALL_BANKS, Dataset
from parsers.competitors import parse_competitors_csv


def test_apply_maps_known_values_keeps_rest():
    m = ComfyMapping(bank="Monobank", table={10: 5, 18: 9})
    s = pd.Series([10, 18, 7, None], dtype="Int64")
    out = m.apply(s)
    assert out.tolist()[:3] == [5, 9, 7]
    assert pd.isna(out.iloc[3])


def test_identity_table_is_inactive():
    m = ComfyMapping(bank="Monobank", table={10: 10, 7: 7})
    assert m.changed_count == 0
    assert not m.is_active
    assert not m.applies_to("Monobank")


def test_applies_only_to_its_bank():
    m = ComfyMapping(bank="Monobank", table={10: 5})
    assert m.applies_to("Monobank")
    assert not m.applies_to("ПриватБанк")
    assert not m.applies_to(ALL_BANKS)


def test_sync_values_adds_identity_defaults():
    m = ComfyMapping(bank="Monobank", table={10: 5})
    m.sync_values([10, 7, 18])
    assert m.table == {10: 5, 7: 7, 18: 18}


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "cfg" / "mapping.json"
    m = ComfyMapping(bank="Monobank", table={10: 5, 18: 9})
    m.save(path)
    loaded = ComfyMapping.load(path)
    assert loaded.bank == "Monobank"
    assert loaded.table == {10: 5, 18: 9}


def test_load_missing_or_corrupt_returns_default(tmp_path):
    assert ComfyMapping.load(tmp_path / "nope.json").bank is None
    bad = tmp_path / "bad.json"
    bad.write_text("{не json", encoding="utf-8")
    assert ComfyMapping.load(bad).table == {}


def test_guess_reduced_bank():
    assert guess_reduced_bank(["ПриватБанк", "Monobank", "ПУМБ"]) == "Monobank"
    assert guess_reduced_bank(["MONOBANK"]) == "MONOBANK"
    assert guess_reduced_bank(["ПриватБанк", "ПУМБ"]) is None
    assert guess_reduced_bank([]) is None


def test_dataset_wide_applies_mapping_only_to_target_bank(kniga_path):
    ds = Dataset(parse_competitors_csv(kniga_path))
    mapping = ComfyMapping(bank="Monobank", table={10: 5, 7: 4, 18: 9})

    mono = ds.wide("Monobank", mapping).set_index("sku")
    assert mono.loc["20671", "pay_comfy"] == 5          # было 10
    assert mono.loc["20671", "dev_bank"] == -1          # 5 − foxtrot(6)
    assert mono.loc["200572", "pay_comfy"] == 4         # было 7
    assert mono.loc["912618", "pay_comfy"] == 9         # было 18
    assert mono.loc["20671", "comfy_max"] == 10         # исходное поле не трогаем

    privat = ds.wide("ПриватБанк", mapping).set_index("sku")
    assert privat.loc["20671", "pay_comfy"] == 10       # другой банк — без маппинга

    all_banks = ds.wide(ALL_BANKS, mapping).set_index("sku")
    assert all_banks.loc["20671", "pay_comfy"] == 10    # «Все банки» = макс. доступность
