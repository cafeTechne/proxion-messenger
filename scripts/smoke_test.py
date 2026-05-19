#!/usr/bin/env python3
"""
Playwright smoke test -- quick single-user flow per TESTING.md.

Checks:
  1. Page loads (title, CSP header)
  2. Onboarding: Welcome -> Name -> Presence -> Skip pod -> Create room -> Open app
  3. Chat UI: sidebar + message input visible after onboarding
  4. Send a message -> appears in thread
  5. Right-click message -> React context menu -> emoji picker opens
  6. No unfiltered console errors
"""
import sys
import os
import time
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://127.0.0.1:8080"
DISPLAY_NAME = "SmokeBot"
ROOM_NAME = "smoke-room"
MSG_TEXT = "hello from smoke test"
TIMEOUT = 12_000  # ms


def wait_for_gateway(timeout_sec=10):
    """Poll gateway until it responds or timeout."""
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            urllib.request.urlopen(BASE_URL + "/", timeout=1)
            return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    return False


def run_smoke(headless=True, external=False):
    gateway_proc = None
    temp_dir = None
    errors = []

    try:
        # Start gateway if not external
        if not external:
            temp_dir = tempfile.mkdtemp(prefix="proxion_smoke_")
            project_root = Path(__file__).parent.parent.resolve()
            web_dir = project_root / "web"

            env = os.environ.copy()
            env["PROXION_DATA_DIR"] = temp_dir
            env["PROXION_HTTP_PORT"] = "8080"
            env["PROXION_WEB_DIR"] = str(web_dir)

            print(f"Starting gateway (temp_dir={temp_dir})...")
            gateway_proc = subprocess.Popen(
                [sys.executable, "run_gateway.py"],
                cwd=str(project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            if not wait_for_gateway(timeout_sec=10):
                print("ERROR: Gateway did not become ready within 10s")
                errors.append("Gateway startup timeout")
                return errors

            print("Gateway ready")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context()

            console_errors = []
            page = ctx.new_page()
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

            # ── 1. Page loads ─────────────────────────────────────────────────────
            print("1. Loading page...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=TIMEOUT)
            assert "Proxion" in page.title(), f"Bad title: {page.title()}"
            print(f"   title: {page.title()!r}  OK")

            # ── 2. CSP header ──────────────────────────────────────────────────────
            print("2. Checking CSP header...")
            resp = urllib.request.urlopen(BASE_URL + "/")
            csp = resp.headers.get("Content-Security-Policy", "")
            assert csp, "Missing Content-Security-Policy header"
            print(f"   CSP present: {csp[:60]}...  OK")

            # ── 3. Onboarding: Welcome ────────────────────────────────────────────
            # Modal is shown after WS `registered` event — wait for it to appear.
            print("3. Onboarding -- Welcome (waiting for WS registered)...")
            modal = page.locator("#onboarding-modal")
            expect(modal).to_be_visible(timeout=TIMEOUT)
            start_btn = page.locator("#ob-start-btn")
            start_btn.click()

            # ── 4. Onboarding: Display name ───────────────────────────────────────
            print("4. Onboarding -- Display name...")
            name_input = page.locator("#ob-name")
            expect(name_input).to_be_visible(timeout=TIMEOUT)
            name_input.fill(DISPLAY_NAME)
            page.locator("#ob-step2-btn").click()

            # ── 5. Onboarding: Presence ───────────────────────────────────────────
            print("5. Onboarding -- Presence...")
            step3_btn = page.locator("#ob-step3-btn")
            expect(step3_btn).to_be_visible(timeout=TIMEOUT)
            step3_btn.click()

            # ── 6. Onboarding: Skip pod ───────────────────────────────────────────
            print("6. Onboarding -- Skip pod (use locally)...")
            skip_pod = page.locator("#ob-skip-pod")
            expect(skip_pod).to_be_visible(timeout=TIMEOUT)
            skip_pod.click()

            # ── 7. Onboarding: Create a room ──────────────────────────────────────
            print("7. Onboarding -- Create a Room...")
            create_btn = page.locator("#ob-step5-create")
            expect(create_btn).to_be_visible(timeout=TIMEOUT)
            create_btn.click()

            # Room create modal opens
            room_input = page.locator("#room-name-input")
            expect(room_input).to_be_visible(timeout=TIMEOUT)
            room_input.fill(ROOM_NAME)
            page.locator("#room-create-submit").click()

            # ── 8. Onboarding: Step 6 - Open Proxion ─────────────────────────────
            print("8. Onboarding -- Open Proxion...")
            finish_btn = page.locator("#ob-finish-btn")
            expect(finish_btn).to_be_visible(timeout=TIMEOUT)
            finish_btn.click()

            # ── 9. Main app UI ─────────────────────────────────────────────────────
            print("9. Verifying main app UI...")
            sidebar = page.locator("#sidebar")
            expect(sidebar).to_be_visible(timeout=TIMEOUT)
            msg_input = page.locator("#message-input")
            expect(msg_input).to_be_visible(timeout=TIMEOUT)
            # Wait for room to auto-select (header changes from "Welcome")
            expect(page.locator("#chat-header-name")).not_to_have_text("Welcome", timeout=TIMEOUT)
            print("   Sidebar + message input visible, room selected  OK")

            # ── 10. Send a message ─────────────────────────────────────────────────
            print("10. Sending a message...")
            msg_input.fill(MSG_TEXT)
            msg_input.press("Enter")

            msg_locator = page.locator(".msg-text", has_text=MSG_TEXT)
            expect(msg_locator).to_be_visible(timeout=TIMEOUT)
            print(f"   Message visible in thread  OK")

            # ── 11. Reaction via context menu ──────────────────────────────────────
            print("11. Opening reaction picker via right-click...")
            msg_locator.click(button="right")
            ctx_menu = page.locator("#ctx-menu")
            expect(ctx_menu).to_be_visible(timeout=TIMEOUT)
            react_btn = page.locator("#ctx-react")
            expect(react_btn).to_be_visible(timeout=TIMEOUT)
            react_btn.click()
            emoji_picker = page.locator("#emoji-picker")
            expect(emoji_picker).to_be_visible(timeout=TIMEOUT)
            print("   Emoji picker opened  OK")
            page.keyboard.press("Escape")

            # ── 12. Console errors ─────────────────────────────────────────────────
            print("12. Checking console errors...")
            # Pod write failures are expected (no pod configured); filter them.
            real_errors = [
                e for e in console_errors
                if "pod write failed" not in e.lower()
                and "pod index" not in e.lower()
                and "failed to load resource" not in e.lower()
                and "[pod]" not in e.lower()
            ]
            if real_errors:
                print(f"   CONSOLE ERRORS ({len(real_errors)}):")
                for e in real_errors:
                    print(f"     {e}")
                errors.append(f"{len(real_errors)} console error(s)")
            else:
                print(f"   No console errors  OK  ({len(console_errors)} pod/resource warnings filtered)")

            browser.close()

    finally:
        # Kill gateway process and clean up temp dir
        if gateway_proc is not None:
            gateway_proc.terminate()
            try:
                gateway_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gateway_proc.kill()

        if temp_dir is not None and os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    return errors


if __name__ == "__main__":
    external = "--external" in sys.argv
    headless = "--headed" not in sys.argv
    print(f"Running smoke test against {BASE_URL} (headless={headless}, external={external})\n")
    errs = run_smoke(headless=headless, external=external)
    print()
    if errs:
        print(f"FAILED: {errs}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)
