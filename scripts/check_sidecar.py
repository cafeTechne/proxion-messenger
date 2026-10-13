#!/usr/bin/env python3
"""Start the built gateway sidecar and check it serves the web app.

A sidecar can build cleanly and still die on launch (a dependency PyInstaller
did not bundle), and nothing else in the release runs it. This starts the
binary the way the desktop app does, with a throwaway data dir and free ports,
waits for PROXION_GATEWAY_READY and for the web page, then stops it.

Usage:  python scripts/check_sidecar.py [path-to-sidecar]
"""
from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIDECAR_DIR = ROOT / "tauri-app" / "src-tauri" / "sidecar"
TIMEOUT = 120


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_sidecar() -> Path:
    found = sorted(SIDECAR_DIR.glob("proxion-gateway-*"))
    if len(found) != 1:
        sys.exit(f"[sidecar-check] expected one sidecar in {SIDECAR_DIR}, found {len(found)}")
    return found[0]


def _fetch(url: str) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # the gateway's own self-signed cert
    with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
        return r.read().decode("utf-8", "replace")


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else _find_sidecar()
    http_port = _free_port()
    data = tempfile.mkdtemp(prefix="proxion-sidecar-check-")
    env = dict(os.environ,
               PROXION_HTTP_PORT=str(http_port),
               PROXION_WS_PORT=str(_free_port()),
               PROXION_HOST="127.0.0.1",
               PROXION_DATA_DIR=data)
    print(f"[sidecar-check] starting {exe.name} on port {http_port}")
    proc = subprocess.Popen([str(exe)], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace")
    lines: list[str] = []
    threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True).start()

    ok = False
    deadline = time.monotonic() + TIMEOUT
    try:
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(1)
            if not any("PROXION_GATEWAY_READY" in ln for ln in lines):
                continue
            for scheme in ("https", "http"):
                try:
                    page = _fetch(f"{scheme}://127.0.0.1:{http_port}/")
                except Exception:
                    continue
                if "<title>Proxion</title>" in page:
                    ok = True
                    print(f"[sidecar-check] OK: {scheme} page served ({len(page)} bytes)")
                break
            if ok:
                break
    finally:
        _stop(proc)

    if not ok:
        state = f"exited with code {proc.returncode}" if proc.returncode is not None else "timed out"
        print(f"[sidecar-check] FAILED: {state}. Output:", file=sys.stderr)
        print("".join(lines[-40:]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
