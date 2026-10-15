# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs

PROJECT = Path(SPECPATH)
binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    [str(PROJECT / "ChessSnip.pyw")],
    pathex=[str(PROJECT)],
    binaries=binaries,
    datas=[
        (str(PROJECT / "chess_position_classifier.onnx"), "."),
        (str(PROJECT / "THIRD_PARTY_NOTICES.txt"), "."),
        (str(PROJECT / "ChessSnip.png"), "."),
    ],
    hiddenimports=["chess_recognition", "onnxruntime.capi._pybind_state"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
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
