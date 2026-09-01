# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

repository_root = Path(SPECPATH).resolve().parent

hidden_imports = collect_submodules("webview")
datas = collect_data_files("webview")
binaries = collect_dynamic_libs("webview")

analysis = Analysis(
    [str(repository_root / "backend" / "desktop" / "__main__.py")],
    pathex=[str(repository_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ReportingSystem",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    uac_admin=False,
    uac_uiaccess=False,
    contents_directory="runtime",
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="ReportingSystem",
)
