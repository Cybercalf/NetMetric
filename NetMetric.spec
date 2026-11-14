# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

root = Path(SPECPATH)
read_only = os.environ.get('NETMETRIC_READONLY') == '1'
name = 'NetMetric-ReadOnly' if read_only else 'NetMetric'
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=[],
    datas=[(str(root / 'assets/app.ico'), 'assets')],
    hiddenimports=['win32com.client', 'win32com.client.dynamic', 'pythoncom', 'pywintypes'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name=name, debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    uac_admin=not read_only, icon=str(root / 'assets/app.ico'),
    version=str(root / 'assets/version_info.txt'),
)
