"""Session-scoped and function-scoped fixtures for E2E tests."""

import asyncio
import socket
import pytest
import os
import threading

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from .helpers import WsSession, connect_and_register


def _find_free_port(host: str = "127.0.0.1") -> int:
    """Find a free port on the given host using socket bind trick."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture(scope="session")
def live_gateway(tmp_path_factory):
    """
    Session-scoped fixture that starts a real ProxionGateway server on a random port.

    Runs the server in a separate thread to avoid blocking the event loop.
    Yields a dict with "url" (ws://127.0.0.1:port) and "gateway" instance.
    """
    import websockets

    # Find a free port
    port = _find_free_port("127.0.0.1")
    url = f"ws://127.0.0.1:{port}"

    # Create agent and gateway
    agent = AgentState.generate()
    agent.webid = "https://gateway.test/profile/card#me"

    db_path = str(tmp_path_factory.mktemp("e2e") / "e2e.db")
    config = GatewayConfig(
        host="127.0.0.1",
        port=port,
        db_path=db_path,
        http_port=None,  # Don't start HTTP server in E2E tests
    )

    gateway = ProxionGateway(
        agent=agent,
        dm_clients={},
        room_memberships={},
        config=config,
        read_state=ReadState(),
    )

    # Run server in a background thread with its own event loop
    server_ready = threading.Event()
    server_error = None
    server_loop = None

    def run_server_thread():
        nonlocal server_error, server_loop
        try:
            server_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(server_loop)
            stop_event = asyncio.Event()

            async def start():
                async with websockets.serve(gateway.handle_client, "127.0.0.1", port):
                    server_ready.set()
                    await stop_event.wait()

            server_loop.run_until_complete(start())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            server_error = e
            server_ready.set()

    thread = threading.Thread(daemon=True, target=run_server_thread)
    thread.start()

    # Wait for server to be ready
    if not server_ready.wait(timeout=5.0):
        raise TimeoutError("Gateway server failed to start in 5 seconds")

    if server_error:
        raise server_error

    yield {"url": url, "gateway": gateway}

    # Cleanup (thread is daemon so will auto-close)
    if server_loop:
        # Schedule cancellation in the server loop thread
        def cancel_server():
            for task in asyncio.all_tasks(server_loop):
                task.cancel()

        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), server_loop)
        server_loop.call_soon_threadsafe(cancel_server)


@pytest.fixture
async def alice_agent():
    """Generate a fresh agent for Alice."""
    return AgentState.generate()


@pytest.fixture
async def bob_agent():
    """Generate a fresh agent for Bob."""
    return AgentState.generate()


@pytest.fixture
async def alice_session(live_gateway, alice_agent):
    """
    Function-scoped fixture: Alice connects, authenticates, and registers.
    """
    session = await connect_and_register(
        live_gateway["url"],
        "Alice",
        alice_agent,
    )
    yield session
    try:
        await session.ws.close()
    except Exception:
        pass


@pytest.fixture
async def bob_session(live_gateway, bob_agent):
    """
    Function-scoped fixture: Bob connects, authenticates, and registers.
    """
    session = await connect_and_register(
        live_gateway["url"],
        "Bob",
        bob_agent,
    )
    yield session
    try:
        await session.ws.close()
    except Exception:
        pass
