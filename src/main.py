"""Точка входа Payment Terms Dashboard.

Запуск из исходников:  python src/main.py
Сборка exe:            pyinstaller build/app.spec
"""

import sys
from pathlib import Path

# при запуске `python src/main.py` каталог src уже в sys.path; при запуске
# из корня репозитория или из exe добавляем его явно
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import flet as ft  # noqa: E402

from ui.dashboard import mount  # noqa: E402


def main(page: ft.Page) -> None:
    page.window.width = 1280
    page.window.height = 860
    page.window.min_width = 980
    page.window.min_height = 640
    mount(page)


if __name__ == "__main__":
    ft.app(target=main)
