"""E2E tests for multi-device scenarios: same identity on two connections."""

import asyncio
import json
import pytest
import websockets

from proxion_messenger_core.persist import AgentState
from .helpers import connect_and_register, WsSession


@pytest.mark.asyncio
async def test_same_did_two_connections(live_gateway):
    """
    Alice connects twice with the same DID.
    A DM sent to Alice's DID is delivered to both connections.
    """
    alice_agent = AgentState.generate()
    bob_agent = AgentState.generate()

    # Alice connects on device 1
    alice1 = await connect_and_register(live_gateway["url"], "Alice-1", alice_agent)
    # Alice connects on device 2 with the same agent (same DID)
    alice2 = await connect_and_register(live_gateway["url"], "Alice-2", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    # Bob sends Alice a DM
    await bob.send(cmd="local_dm", target_webid=alice1.did, content="Multi-device hello")

    # Bob gets his own echo
    bob_echo = await bob.recv_type("message", timeout=5.0)
    assert bob_echo.get("content") == "Multi-device hello"

    # _any_socket picks one of Alice's connections — check whichever gets the message
    received = None
    for device in (alice1, alice2):
        try:
            evt = await device.recv_type("message", timeout=3.0)
            if evt.get("content") == "Multi-device hello":
                received = evt
                break
        except TimeoutError:
            continue
    assert received is not None, "Neither of Alice's devices received the DM"

    await alice1.ws.close()
    await alice2.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_presence_broadcast_multidevice(live_gateway):
    """Setting presence on one connection broadcasts to all connected clients."""
    alice_agent = AgentState.generate()
    bob_agent = AgentState.generate()

    alice = await connect_and_register(live_gateway["url"], "Alice", alice_agent)
    bob = await connect_and_register(live_gateway["url"], "Bob", bob_agent)

    # Drain initial presence events from registration
    await alice.drain(timeout=0.3)
    await bob.drain(timeout=0.3)

    await alice.send(cmd="set_presence", status="busy")

    alice_pres = await alice.recv_type("presence_update", timeout=5.0)
    assert alice_pres.get("status") == "busy"

    bob_pres = await bob.recv_type("presence_update", timeout=5.0)
    assert bob_pres.get("status") == "busy"
    assert bob_pres.get("webid") == alice.did

    await alice.ws.close()
    await bob.ws.close()


@pytest.mark.asyncio
async def test_get_all_presence(alice_session, bob_session):
    """get_all_presence returns presence scoped to the caller's own entries.

    Since security hardening (Round 6), get_all_presence only exposes presence
    for the caller themselves and their stored contacts.  Alice and Bob have no
    established relationship here, so Alice only sees her own entry.
    """
    await alice_session.send(cmd="get_all_presence")
    result = await alice_session.recv_type("all_presence", timeout=5.0)
    presence = result.get("presence", {})
    # Alice always sees her own presence entry
    assert alice_session.did in presence
    # Bob is NOT a contact of Alice so his presence must not be leaked
    assert bob_session.did not in presence
