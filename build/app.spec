# -*- mode: python ; coding: utf-8 -*-
"""Конфиг PyInstaller: один exe без консоли (запуск двойным кликом).

Сборку выполнять на Windows 10/11 x64:

    pip install -r requirements-dev.txt
    pyinstaller build/app.spec        # → dist/PaymentTermsDashboard.exe

Альтернатива (генерирует свой spec, тоже onefile):

    flet pack src/main.py --name PaymentTermsDashboard
"""

import os

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# flet_desktop содержит клиент Flutter — без него окно не откроется
for pkg in ("flet", "flet_desktop"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:  # noqa: BLE001 — пакет может отсутствовать вне Windows
        pass

SRC = os.path.join(SPECPATH, "..", "src")  # noqa: F821 — SPECPATH задаёт PyInstaller

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["xlsxwriter", "openpyxl"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "jupyter"],
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

