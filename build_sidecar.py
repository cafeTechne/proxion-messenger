#!/usr/bin/env python3
"""
Build the proxion-gateway PyInstaller sidecar for Tauri.

Usage (from the Proxion repo root):
    python build_sidecar.py

Requires:
    pip install pyinstaller
    pip install -e proxion-messenger-core/[gateway]

Output:
    tauri-app/src-tauri/sidecar/proxion-gateway-{triple}[.exe]

Tauri picks this up automatically via externalBin in tauri.conf.json.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Rust target triples by (OS, machine) — extend as needed
TRIPLE_MAP: dict[tuple[str, str], str] = {
    ("Windows", "AMD64"):    "x86_64-pc-windows-msvc",
    ("Windows", "ARM64"):    "aarch64-pc-windows-msvc",
    ("Darwin",  "x86_64"):  "x86_64-apple-darwin",
    ("Darwin",  "arm64"):   "aarch64-apple-darwin",
    ("Linux",   "x86_64"):  "x86_64-unknown-linux-gnu",
    ("Linux",   "aarch64"): "aarch64-unknown-linux-gnu",
}

ROOT        = Path(__file__).resolve().parent
ENTRY       = ROOT / "run_gateway.py"
PROXION_SRC = ROOT / "proxion-messenger-core" / "src"
WEB_DIR     = ROOT / "web"
SIDECAR_DIR = ROOT / "tauri-app" / "src-tauri" / "sidecar"
BUILD_DIR   = ROOT / "build"
DIST_DIR    = BUILD_DIR / "pyinstaller"
WORK_DIR    = BUILD_DIR / "pyinstaller_work"
SPEC_DIR    = BUILD_DIR


def get_triple() -> str:
    key = (platform.system(), platform.machine())
    if key not in TRIPLE_MAP:
        raise SystemExit(f"Unsupported platform: {key[0]} / {key[1]}\n"
                         f"Supported: {list(TRIPLE_MAP.keys())}")
    return TRIPLE_MAP[key]


# 1980-01-01, the floor ZIP/PyInstaller archive timestamps can represent.
_EPOCH_FLOOR = "315532800"


def source_date_epoch() -> str:
    """A fixed build timestamp for reproducibility (E4).

    Toolchains that honor SOURCE_DATE_EPOCH embed this instead of the wall-clock
    build time, so two builds of the same commit agree on archive metadata. We
    derive it from the commit timestamp; without git we fall back to the ZIP
    epoch floor. Overridable via the environment for a caller that wants to pin
    a specific value.
    """
    env = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if env.isdigit():
        return env
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        ts = out.stdout.strip()
        if ts.isdigit():
            return ts
    except Exception:
        pass
    return _EPOCH_FLOOR


def build_env() -> dict:
    """Environment for the PyInstaller subprocess, with determinism knobs set
    (E4). Kept separate so the reproducibility harness can reuse it."""
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = source_date_epoch()
    # Stable string hashing so any hash-ordered output is build-invariant.
    env.setdefault("PYTHONHASHSEED", "0")
    return env


# All proxion_messenger_core sub-modules that are imported dynamically at runtime.
# PyInstaller's static analysis misses lazy imports inside elif/try blocks.
HIDDEN_IMPORTS = [
    "proxion_messenger_core.gateway",
    "proxion_messenger_core.local_store",
    "proxion_messenger_core.persist",
    "proxion_messenger_core.readstate",
    "proxion_messenger_core.didkey",
    "proxion_messenger_core.relay",
    "proxion_messenger_core.voice",
    "proxion_messenger_core.pins",
    "proxion_messenger_core.reactions",
    "proxion_messenger_core.invites",
    "proxion_messenger_core.invitecode",
    "proxion_messenger_core.notifications",
    "proxion_messenger_core.blocklist",
    "proxion_messenger_core.search",
    "proxion_messenger_core.msgcrypto",
    "proxion_messenger_core.linkpreview",
    "proxion_messenger_core.files",
    "proxion_messenger_core.receipts",
    "proxion_messenger_core.peerdb",
    "proxion_messenger_core.profile",
    "proxion_messenger_core.outbox",
    "proxion_messenger_core.solid_client",
    "proxion_messenger_core.css_auth",
    "proxion_messenger_core.css_setup",
    "proxion_messenger_core.room",
    "proxion_messenger_core.room_store",
    "proxion_messenger_core.federation",
    "proxion_messenger_core.inbox",
    "proxion_messenger_core.presence",
    "proxion_messenger_core.identity",
    "proxion_messenger_core.discovery",
    "proxion_messenger_core.export",
    "proxion_messenger_core.solid",
    "proxion_messenger_core.solid_auth",
    "proxion_messenger_core.solid_store",
    "proxion_messenger_core.pod_room_store",
    "proxion_messenger_core.acp",
    "proxion_messenger_core.mirror",
    "proxion_messenger_core.oidc",
    "proxion_messenger_core.replies",
    "proxion_messenger_core.dpop",
    "proxion_messenger_core.messaging",
    "proxion_messenger_core.certtoken",
    "proxion_messenger_core.handshake",
    "proxion_messenger_core.tokens",
    "proxion_messenger_core.crypto",
    "proxion_messenger_core.attenuation",
    "proxion_messenger_core.store",
    "proxion_messenger_core.store_sqlite",
    "proxion_messenger_core.store_client",
    "proxion_messenger_core.validator",
    "proxion_messenger_core.device",
    "proxion_messenger_core.context",
    "proxion_messenger_core.sealed",
    "proxion_messenger_core.revoke",
    "proxion_messenger_core.revocation",
    "proxion_messenger_core.pop",
    "cryptography",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "cryptography.hazmat.primitives.asymmetric.x25519",
    "cryptography.hazmat.primitives.serialization",
    "websockets",
    "websockets.asyncio",
    "websockets.asyncio.server",
    "websockets.asyncio.client",
    "httpx",
]


def build() -> None:
    triple = get_triple()
    is_windows = platform.system() == "Windows"
    exe_suffix = ".exe" if is_windows else ""
    sep = ";" if is_windows else ":"

    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--name", "proxion-gateway",
        "--distpath", str(DIST_DIR),
        "--workpath", str(WORK_DIR),
        "--specpath", str(SPEC_DIR),
        "--paths", str(PROXION_SRC),
    ]

    # Bundle the web UI so the gateway can serve it standalone / in Tauri dev mode
    if WEB_DIR.exists():
        cmd += ["--add-data", f"{WEB_DIR}{sep}web"]
    else:
        print(f"[warn] {WEB_DIR} not found — gateway won't serve web UI from bundle")

    # R18.3.4: embed version.txt so gateway_version is available at runtime
    version_file = ROOT / "version.txt"
    if version_file.exists():
        cmd += ["--add-data", f"{version_file}{sep}."]
    else:
        print("[warn] version.txt not found — /.well-known/proxion will report 0.1.0")

    # Sorted so the build inputs are in a stable order regardless of how the
    # list is maintained (E4 determinism).
    for mod in sorted(HIDDEN_IMPORTS):
        cmd += ["--hidden-import", mod]

    cmd.append(str(ENTRY))

    env = build_env()
    print(f"Building proxion-gateway sidecar for {triple}...")
    print(f"  entry:   {ENTRY}")
    print(f"  dist:    {DIST_DIR}")
    print(f"  SOURCE_DATE_EPOCH: {env['SOURCE_DATE_EPOCH']}")
    print()
    subprocess.run(cmd, check=True, env=env)

    src = DIST_DIR / f"proxion-gateway{exe_suffix}"
    dst = SIDECAR_DIR / f"proxion-gateway-{triple}{exe_suffix}"

    shutil.copy2(src, dst)
    size_mb = dst.stat().st_size / 1024 / 1024
    print(f"\nSidecar ready: {dst}  ({size_mb:.1f} MB)")
    print(f"\nNext: cd tauri-app && npm run tauri build")


if __name__ == "__main__":
    build()
