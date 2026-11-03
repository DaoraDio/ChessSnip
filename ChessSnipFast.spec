# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

PROJECT = Path(SPECPATH)
binaries = collect_dynamic_libs("onnxruntime")
import winrt
winrt_dir = Path(next(iter(winrt.__path__)))
# The WinRT wheel ships an older MSVC runtime. Keep the app-level runtime
# collected with ONNX Runtime shared, so ONNX cannot bind to the stale copy.
winrt_binaries = [
    item for item in collect_dynamic_libs("winrt")
    if Path(item[0]).name.casefold() != "msvcp140.dll"
] + [
    (str(path), "winrt") for path in winrt_dir.glob("_winrt*.pyd")
]
winrt_datas = [
    item for item in collect_data_files("winrt", include_py_files=True)
    if not item[0].lower().endswith(".dll")
]
qr_datas, qr_binaries, qr_hiddenimports = collect_all("zxingcpp")
lichess_image_datas = [
    (str(path), "assets/lichess")
    for path in (PROJECT / "assets" / "lichess").glob("*.png")
]
a = Analysis(
    [str(PROJECT / "ChessSnip.pyw")],
    pathex=[str(PROJECT)],
    binaries=binaries + winrt_binaries + qr_binaries,
    datas=[
        (str(PROJECT / "chess_position_classifier.onnx"), "."),
        (str(PROJECT / "THIRD_PARTY_NOTICES.txt"), "."),
        (str(PROJECT / "PyWinRT_LICENSE.txt"), "."),
        (str(PROJECT / "ZXing_CPP_LICENSE.txt"), "."),
        (str(PROJECT / "ChessSnip.png"), "."),
    ] + lichess_image_datas + qr_datas + winrt_datas,
    hiddenimports=["chess_recognition", "onnxruntime.capi._pybind_state"] + qr_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # OCR uses Windows.Media.Ocr; keep any locally installed RapidOCR package out.
    excludes=["rapidocr"],
    noarchive=False,
    optimize=0,
)
# PyInstaller's dependency scan can re-add the WinRT wheel's older MSVC runtime.
from PyInstaller.building.datastruct import TOC
a.binaries = TOC(
    entry for entry in a.binaries
    if entry[0].replace('\\', '/').casefold() != 'winrt/msvcp140.dll'
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChessSnip",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT / "ChessSnip.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ChessSnip",
)
