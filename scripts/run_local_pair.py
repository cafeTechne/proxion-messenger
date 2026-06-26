"""
Start both Alice (port 7474) and Bob (port 7475) gateways in one terminal.

Usage:
    python scripts/run_local_pair.py

Run setup_local_test.py first to create test-data/alice.json and test-data/bob.json.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "proxion-messenger-core" / "src"))

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import GatewayConfig, run_gateway
from proxion_messenger_core.readstate import ReadState

WEB_DIR = str(ROOT / "web")

USERS = [
    ("alice", "alice-dev-pass", 7474, 8474),
    ("bob",   "bob-dev-pass",   7475, 8475),
]


async def main():
    tasks = []
    for name, passphrase, ws_port, http_port in USERS:
        path = ROOT / "test-data" / f"{name}.json"
        if not path.exists():
            print(f"ERROR: {path} not found")
            print(f"  Run: python scripts/setup_local_test.py")
            return
        agent = AgentState.load(path, passphrase.encode())
        config = GatewayConfig(
            host="127.0.0.1",
            port=ws_port,
            poll_interval=60.0,
            http_port=http_port,
            web_dir=WEB_DIR,
            db_path=str(ROOT / "test-data" / f"{name}_local.db"),
        )
        print(f"Starting {name}: ws://127.0.0.1:{ws_port}  web -> http://127.0.0.1:{http_port}")
        tasks.append(run_gateway(agent, [], [], config, ReadState()))

    print()
    print("Open these URLs in two separate browser windows:")
    print("  Alice -> http://127.0.0.1:8474")
    print("  Bob   -> http://127.0.0.1:8475")
    print()
    print("Click the gear icon to see your DID, then click + in the DM section")
    print("and paste the other person's DID to start chatting.")
    print()
    print("Press Ctrl+C to stop.")
    await asyncio.gather(*tasks)


asyncio.run(main())
