"""
Full CSS pod setup + bidirectional handshake between Alice and Bob.

Requires the CSS pods to be running:
    docker compose -f docker-compose.full.yml up -d alice-pod bob-pod

After running this script, restart gateways to pick up the new certificates:
    python scripts/run_alice_gateway.py
    python scripts/run_bob_gateway.py

Usage:
    python scripts/setup_css_test.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "proxion-messenger-core" / "src"))

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.css_setup import CssAccountManager, build_dpop_client
from proxion_messenger_core.handshake import run_bidirectional_handshake

ALICE_POD   = "http://localhost:3001"
BOB_POD     = "http://localhost:3002"
ALICE_EMAIL = "alice@proxion.local"
BOB_EMAIL   = "bob@proxion.local"
ALICE_PASS  = "alice-dev-pass"
BOB_PASS    = "bob-dev-pass"
DATA_DIR    = ROOT / "test-data"


def load_or_create(name: str, passphrase: str) -> AgentState:
    path = DATA_DIR / f"{name}.json"
    if path.exists():
        return AgentState.load(path, passphrase.encode())
    agent = AgentState.generate()
    agent.save(path, passphrase.encode())
    return agent


async def main():
    DATA_DIR.mkdir(exist_ok=True)

    alice = load_or_create("alice", ALICE_PASS)
    bob   = load_or_create("bob",   BOB_PASS)

    # Create CSS accounts
    alice_creds = None
    bob_creds   = None

    print("Creating CSS accounts...")
    try:
        alice_creds = CssAccountManager(ALICE_POD).create_account(alice, ALICE_EMAIL, ALICE_PASS)
        print(f"  Alice account created at {ALICE_POD}")
    except Exception as e:
        print(f"  Alice: {e}")

    try:
        bob_creds = CssAccountManager(BOB_POD).create_account(bob, BOB_EMAIL, BOB_PASS)
        print(f"  Bob account created at {BOB_POD}")
    except Exception as e:
        print(f"  Bob: {e}")

    if not alice_creds or not bob_creds:
        print("\nCould not obtain credentials for both users.")
        print("If accounts already exist, delete data/alice-pod and data/bob-pod volumes and retry.")
        return

    alice_client = build_dpop_client(alice, alice_creds)
    bob_client   = build_dpop_client(bob,   bob_creds)

    print("\nRunning bidirectional handshake...")
    await run_bidirectional_handshake(alice, alice_client, bob, bob_client)
    print("Handshake complete!")

    alice.save(DATA_DIR / "alice.json", ALICE_PASS.encode())
    bob.save(  DATA_DIR / "bob.json",   BOB_PASS.encode())
    print("\nAgent states saved with certificates.")
    print("Restart gateway scripts to use the new certs:")
    print("  python scripts/run_alice_gateway.py")
    print("  python scripts/run_bob_gateway.py")


asyncio.run(main())
