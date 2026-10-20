"""Session-scoped and function-scoped fixtures for E2E tests."""

import pytest

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState
from .helpers import WsSession, connect_and_register


@pytest.fixture(scope="session")
def live_gateway(tmp_path_factory):
    """
    Session-scoped fixture that starts a real ProxionGateway server on a random port.

    Started through tests/gwharness.py so a startup failure raises here and the
    gateway is stopped at session end. Auth is forced on (as a public tunnel
    does), so every E2E session completes the did:key challenge and the
    handlers run their auth-enforced branches, which unit tests mostly skip.
    Yields a dict with "url" (ws://127.0.0.1:port) and "gateway" instance.
    """
    from gwharness import start_gateway

    # Port 0: the OS assigns a free port at bind time and the harness writes it
    # back into config.port, so nothing can grab it between pick and bind.
    port = 0

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
    gateway._force_auth = True

    handle = start_gateway(gateway, port, 0, serve_http=False, auto_shutdown=False)
    url = f"ws://127.0.0.1:{handle.ws_port}"
    assert gateway.config.port == handle.ws_port != 0
    try:
        yield {"url": url, "gateway": gateway}
    finally:
        handle.stop()


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
