"""Настраиваемый маппинг доступности Comfy для банка со сниженными условиями.

Поле «Comfy. MAX платежей» в выгрузке — это максимальная доступность Comfy,
т.е. условия в банках уровня ПриватБанк/ПУМБ. В Monobank доступность Comfy
снижена, поэтому сравнивать конкурентов в разрезе Monobank с максимальной
доступностью Comfy некорректно. Маппинг задаёт соответствие
«макс. доступность → доступность в выбранном банке» и применяется ко всем
расчётам (KPI, таблица, отклонения, сводка, экспорт), когда выбран этот банк.

Настройки сохраняются в JSON в каталоге пользователя и переживают перезапуск.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".payment_terms_dashboard"
MAPPING_FILE = CONFIG_DIR / "comfy_mapping.json"


@dataclass
class ComfyMapping:
    """`table[макс. платежей] = платежей в банке bank` (обычно Monobank)."""

    bank: str | None = None
    table: dict[int, int] = field(default_factory=dict)

    @property
    def changed_count(self) -> int:
        return sum(1 for k, v in self.table.items() if k != v)

    @property
    def is_active(self) -> bool:
        return bool(self.bank) and self.changed_count > 0

    def applies_to(self, bank: str | None) -> bool:
        return self.is_active and bank == self.bank

    def apply(self, comfy_max: pd.Series) -> pd.Series:
        """Платежи Comfy в банке: маппинг с фолбэком на исходное значение."""
        if not self.table:
            return comfy_max.astype("Float64")
        table = {float(k): float(v) for k, v in self.table.items()}
        values = comfy_max.astype("Float64").astype(float)
        return pd.Series(
            [table.get(v, v) for v in values], index=comfy_max.index, dtype="Float64"
        )

    def sync_values(self, values: list[int]) -> None:
        """Дополняет таблицу новыми значениями из файла (по умолчанию 1:1)."""
        for v in values:
            self.table.setdefault(int(v), int(v))

    # ------------------------------------------------------------- хранение
    def save(self, path: Path = MAPPING_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bank": self.bank,
            "table": {str(k): int(v) for k, v in self.table.items()},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    @classmethod
    def load(cls, path: Path = MAPPING_FILE) -> "ComfyMapping":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            table = {int(k): int(v) for k, v in payload.get("table", {}).items()}
            return cls(bank=payload.get("bank"), table=table)
        except FileNotFoundError:
            return cls()
        except Exception as exc:  # noqa: BLE001 — битый файл не должен ронять запуск
            log.warning("Не удалось прочитать %s: %s — маппинг сброшен.", path, exc)
            return cls()


def guess_reduced_bank(banks: list[str]) -> str | None:
    """Эвристика банка со сниженной доступностью: имя содержит «mono»."""
    for bank in banks:
        if "mono" in bank.lower():
            return bank
    return None
