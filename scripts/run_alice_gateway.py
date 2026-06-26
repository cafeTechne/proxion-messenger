"""Run Alice's gateway on port 7474. Run setup_local_test.py first."""
import asyncio, sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "proxion-messenger-core" / "src"))

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import GatewayConfig, run_gateway
from proxion_messenger_core.readstate import ReadState

STATE = ROOT / "test-data" / "alice.json"
PASSPHRASE = "alice-dev-pass"

async def main():
    agent = AgentState.load(STATE, PASSPHRASE.encode())
    config = GatewayConfig(host="127.0.0.1", port=7474, poll_interval=3.0)
    print(f"Alice gateway running on ws://127.0.0.1:7474")
    await run_gateway(agent=agent, dm_clients=[], room_memberships=[], config=config, read_state=ReadState())

asyncio.run(main())
