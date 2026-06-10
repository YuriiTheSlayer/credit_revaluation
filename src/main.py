"""Точка входа Payment Terms Dashboard (pywebview + JS-фронтенд).

Запуск из исходников:  python src/main.py
Сборка exe:            pyinstaller build/app.spec

Окно — нативный WebView ОС (на Windows — Edge WebView2), фронтенд —
локальные HTML/CSS/JS из ``webui/assets`` без внешних зависимостей и сети.
"""

import sys
from pathlib import Path

# при запуске `python src/main.py` каталог src уже в sys.path; при запуске
# из корня репозитория или из exe добавляем его явно
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import webview  # noqa: E402

from core import brand  # noqa: E402
from webui.api import Api  # noqa: E402


def index_path() -> Path:
    """index.html в дев-режиме и внутри onefile-сборки PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", _SRC))
    candidate = base / "webui" / "assets" / "index.html"
    if not candidate.exists():
        raise FileNotFoundError(f"Не найден фронтенд: {candidate}")
    return candidate


def main() -> None:
    api = Api()
    window = webview.create_window(
        f"{brand.APP_TITLE} — Comfy vs конкуренты",
        url=str(index_path()),
        js_api=api,
        width=1320,
        height=900,
        min_size=(1024, 700),
        background_color=brand.BG,
    )
    api.attach_window(window)
    webview.start()


if __name__ == "__main__":
    main()
