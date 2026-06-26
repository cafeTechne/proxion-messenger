#!/usr/bin/env python3
"""Provision a CSS test pod account and write credentials to web/.env.test."""

import os
import sys
from pathlib import Path

# Add proxion-messenger-core/src to path (two levels up from scripts/)
scripts_dir = Path(__file__).resolve().parent
repo_root = scripts_dir.parent
sys.path.insert(0, str(repo_root / "proxion-messenger-core" / "src"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from proxion_messenger_core.css_setup import CssAccountManager


def main():
    try:
        # Read config from environment with defaults
        css_url = os.getenv("TEST_CSS_URL", "http://localhost:3001")
        email = os.getenv("TEST_POD_EMAIL", "proxion-test@example.com")
        password = os.getenv("TEST_POD_PASSWORD", "proxion-test-pw-9981")

        # Generate fresh identity key for the call
        identity_key = Ed25519PrivateKey.generate()

        # Initialize manager and connect
        mgr = CssAccountManager(css_url)
        creds, pod_url, webid = mgr.connect_agent(identity_key, email, password, label="proxion-test")

        # Compute storage root
        storage_root = pod_url.rstrip("/") + "/proxion/"

        # Build env content
        env_lines = [
            f"TEST_CSS_ISSUER={css_url}",
            f"TEST_CSS_CLIENT_ID={creds.client_id}",
            f"TEST_CSS_CLIENT_SECRET={creds.client_secret}",
            f"TEST_STORAGE_ROOT={storage_root}",
            f"TEST_WEBID={webid}",
        ]

        # Write to web/.env.test
        output_path = repo_root / "web" / ".env.test"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(env_lines) + "\n")

        # Print each line and the output path
        for line in env_lines:
            print(line)
        print(f"\nWrote to {output_path}")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
