"""
Set up local two-user test environment.

Creates two agent state files (alice.json, bob.json) in ./test-data/ and
prints the DID for each so you can use them in the web UI.

Usage:
    python scripts/setup_local_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "proxion-core" / "src"))

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import agent_did

DATA_DIR = ROOT / "test-data"
DATA_DIR.mkdir(exist_ok=True)

USERS = [
    ("alice", "alice-dev-pass"),
    ("bob", "bob-dev-pass"),
]

for name, passphrase in USERS:
    path = DATA_DIR / f"{name}.json"
    if path.exists():
        agent = AgentState.load(path, passphrase.encode())
        print(f"[{name}] Loaded existing agent")
    else:
        agent = AgentState.generate()
        agent.save(path, passphrase.encode())
        print(f"[{name}] Created new agent at {path}")

    did = agent_did(agent)
    print(f"  DID: {did}")
    print(f"  State: {path}")
    print(f"  Passphrase: {passphrase}")
    print()

print("To start gateways, run:")
print("  python scripts/run_alice_gateway.py")
print("  python scripts/run_bob_gateway.py")
print()
print("Then open:")
print("  Alice UI: open web/index.html in browser, set gateway URL to ws://127.0.0.1:7474")
print("  Bob UI:   open web/index.html in a second browser/profile, set gateway URL to ws://127.0.0.1:7475")
