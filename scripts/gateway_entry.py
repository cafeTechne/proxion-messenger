"""
Standalone gateway entry point for PyInstaller .exe build.

Usage:
    proxion-gateway.exe [--state <path>] [--passphrase <pass>]
                        [--host <host>] [--port <port>]
                        [--poll <seconds>]
                        [--turn-url <url>] [--turn-secret <secret>]

All flags are optional; environment variables PROXION_* override defaults.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("proxion.gateway")

# Ensure bundled src is on the path when running from .exe
if getattr(sys, "frozen", False):
    bundle_dir = Path(sys._MEIPASS)
    sys.path.insert(0, str(bundle_dir))

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import GatewayConfig, run_gateway
from proxion_messenger_core.readstate import ReadState


def parse_args():
    p = argparse.ArgumentParser(description="Proxion Gateway")
    p.add_argument("--state", default=os.environ.get("PROXION_STATE_FILE", "agent.json"))
    p.add_argument("--passphrase", default=os.environ.get("PROXION_PASSPHRASE", ""))
    p.add_argument("--host", default=os.environ.get("PROXION_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PROXION_PORT", "7474")))
    p.add_argument("--poll", type=float, default=float(os.environ.get("PROXION_POLL", "3.0")))
    p.add_argument("--turn-url", default=os.environ.get("PROXION_TURN_URL"))
    p.add_argument("--turn-secret", default=os.environ.get("PROXION_TURN_SECRET"))
    return p.parse_args()


def load_or_create_agent(state_path: Path, passphrase: str) -> AgentState:
    if state_path.exists():
        logger.info("Loading agent state from %s", state_path)
        return AgentState.load(state_path, passphrase.encode())
    else:
        logger.info("No state file found — generating fresh AgentState at %s", state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        agent = AgentState.generate()
        agent.save(state_path, passphrase.encode() if passphrase else b"")
        return agent


async def main():
    args = parse_args()
    state_path = Path(args.state)
    agent = load_or_create_agent(state_path, args.passphrase)

    config = GatewayConfig(
        host=args.host,
        port=args.port,
        poll_interval=args.poll,
        turn_url=args.turn_url,
        turn_secret=args.turn_secret,
    )
    logger.info("Proxion Gateway starting on %s:%d", args.host, args.port)
    logger.info("State file: %s", state_path.resolve())
    await run_gateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=config,
        read_state=ReadState(),
    )


if __name__ == "__main__":
    asyncio.run(main())
