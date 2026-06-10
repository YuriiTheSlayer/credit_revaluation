# -*- mode: python ; coding: utf-8 -*-
"""Конфиг PyInstaller: один exe без консоли (запуск двойным кликом).

Сборку выполнять на Windows 10/11 x64:

    pip install -r requirements-dev.txt
    pyinstaller build/app.spec --noconfirm   # → dist/PaymentTermsDashboard.exe

Окно приложения — Edge WebView2 (предустановлен на Windows 11 и обновлённых
Windows 10; при отсутствии ставится бесплатным Evergreen-инсталлятором
Microsoft). Фронтенд (webui/assets) упаковывается внутрь exe.
"""

import os

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# pywebview: платформенные бэкенды подтягиваются динамически
for pkg in ("webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:  # noqa: BLE001 — пакет может отсутствовать вне Windows
        pass

SRC = os.path.join(SPECPATH, "..", "src")  # noqa: F821 — SPECPATH задаёт PyInstaller

# JS-фронтенд внутрь сборки (см. main.index_path)
datas += [(os.path.join(SRC, "webui", "assets"), os.path.join("webui", "assets"))]

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "xlsxwriter",
        "openpyxl",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "jupyter", "flet"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PaymentTermsDashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # --noconsole
    disable_windowed_traceback=False,
)
# onefile-режим: binaries и datas переданы прямо в EXE (без шага COLLECT),
# поэтому на выходе единственный файл dist/PaymentTermsDashboard.exe
