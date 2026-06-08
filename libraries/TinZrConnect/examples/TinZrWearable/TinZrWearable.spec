# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\lua4006\\Desktop\\GitRepo\\TinZr\\libraries\\TinZrConnect\\examples\\TinZrWearable\\TinZrWearable.py'],
    pathex=['C:\\Users\\lua4006\\Desktop\\GitRepo\\TinZr\\libraries\\TinZrConnect\\examples'],
    binaries=[],
    datas=[],
    hiddenimports=['GUIsHelper'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tensorflow', 'torch', 'torchvision', 'torchaudio', 'keras'],
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
    name='TinZrWearable',
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
    icon=['C:\\Users\\lua4006\\Desktop\\GitRepo\\TinZr\\libraries\\TinZrConnect\\examples\\TinZrWearable\\TinZr_small_logo.ico'],
)
