# PyInstaller spec for proxion-gateway.exe
# Build with: pyinstaller scripts/build_gateway.spec

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # repo root
CORE_SRC = str(ROOT / "proxion-core" / "src")

a = Analysis(
    [str(ROOT / "scripts" / "gateway_entry.py")],
    pathex=[CORE_SRC],
    binaries=[],
    datas=[
        # Include the web UI so the .exe can serve it from a sibling /web dir
        (str(ROOT / "web"), "web"),
    ],
    hiddenimports=[
        "proxion_core",
        "proxion_core.gateway",
        "proxion_core.persist",
        "proxion_core.readstate",
        "proxion_core.inbox",
        "proxion_core.msgcrypto",
        "proxion_core.messaging",
        "websockets",
        "websockets.server",
        "websockets.legacy",
        "websockets.legacy.server",
        "cryptography",
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        "cryptography.hazmat.primitives.asymmetric.x25519",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="proxion-gateway",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # keep console window — gateway is a server process
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
