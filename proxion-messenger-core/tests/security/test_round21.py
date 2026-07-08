"""Round 21 security tests: scheduler membership bypass, DM reaction injection,
and room ownership hijacking on creator departure."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gateway(tmp_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    agent = AgentState.generate()
    config = GatewayConfig(db_path=str(tmp_path / "store.db"))
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=config)


def _fake_ws(gw, webid: str):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw._client_webids[ws] = webid
    return ws


def _owner_ws(gw):
    from proxion_messenger_core.didkey import pub_key_to_did
    owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    return _fake_ws(gw, owner_did)


def _add_room(gw, room_id: str, members: set, creator_webid: str = ""):
    gw._local_rooms[room_id] = {
        "name": "test-room",
        "members": members,
        "pinned_messages": [],
        "disappear_ms": 0,
        "creator_webid": creator_webid,
        "messages": [],
        "history_mode": "none",
    }


# ---------------------------------------------------------------------------
# Finding 1: Scheduler membership bypass
# ---------------------------------------------------------------------------

class TestSchedulerMembershipBypass:
    @pytest.mark.asyncio
    async def test_scheduler_bypasses_membership_rejected(self, tmp_path):
        """A kicked user's scheduled message is rejected because _check_room_permission
        now consults the store rather than blindly allowing system_ws."""
        gw = _make_gateway(tmp_path)
        room_id = "room-private"
        kicked_did = "did:key:zkicked"

        # Room exists but kicked user is NOT in DB members
        member_ws = _fake_ws(gw, "did:key:zmember")
        _add_room(gw, room_id, {member_ws}, creator_webid="did:key:zmember")
        if gw._store:
            gw._store.save_room(room_id, "Private", "code-priv", "", "none", "did:key:zmember")
            gw._store.add_room_member(room_id, "did:key:zmember")
            # kicked_did is deliberately NOT added to room_members

        # Simulate scheduler: create null_ws for kicked user
        null_ws = gw._NullWs()
        gw._client_webids[null_ws] = kicked_did
        gw._system_ws.add(null_ws)

        # _check_room_permission must return False for the kicked user
        allowed = gw._check_room_permission(null_ws, room_id, role="member")
        assert not allowed, "Kicked user's system stub should not pass permission check"

        # Clean up
        gw._client_webids.pop(null_ws, None)
        gw._system_ws.discard(null_ws)

    @pytest.mark.asyncio
    async def test_scheduler_active_membership_allowed(self, tmp_path):
        """A valid room member's scheduled message is allowed when they are still in the DB."""
        gw = _make_gateway(tmp_path)
        room_id = "room-open"
        member_did = "did:key:zmember"

        member_ws = _fake_ws(gw, member_did)
        _add_room(gw, room_id, {member_ws}, creator_webid=member_did)
        if gw._store:
            gw._store.save_room(room_id, "Open", "code-open", "", "none", member_did)
            gw._store.add_room_member(room_id, member_did)

        # Simulate scheduler: create null_ws for the valid member
        null_ws = gw._NullWs()
        gw._client_webids[null_ws] = member_did
        gw._system_ws.add(null_ws)

        # _check_room_permission must return True (member is in DB)
        allowed = gw._check_room_permission(null_ws, room_id, role="member")
        assert allowed, "Valid room member's system stub should pass permission check"

        gw._client_webids.pop(null_ws, None)
        gw._system_ws.discard(null_ws)

    @pytest.mark.asyncio
    async def test_scheduler_cannot_gain_owner_role(self, tmp_path):
        """Even if a user is a room member, the scheduler cannot escalate to owner role."""
        gw = _make_gateway(tmp_path)
        room_id = "room-admin"
        member_did = "did:key:zmember"
        creator_did = "did:key:zcreator"

        member_ws = _fake_ws(gw, member_did)
        _add_room(gw, room_id, {member_ws}, creator_webid=creator_did)
        if gw._store:
            gw._store.save_room(room_id, "Admin", "code-admin", "", "none", creator_did)
            gw._store.add_room_member(room_id, member_did)

        null_ws = gw._NullWs()
        gw._client_webids[null_ws] = member_did
        gw._system_ws.add(null_ws)

        # Member should not pass owner check even via system_ws
        allowed = gw._check_room_permission(null_ws, room_id, role="owner")
        assert not allowed

        gw._client_webids.pop(null_ws, None)
        gw._system_ws.discard(null_ws)


# ---------------------------------------------------------------------------
# Finding 2: DM reaction injection & spying
# ---------------------------------------------------------------------------

class TestDMReactionAuthorization:
    def _make_cert_and_rel(self, gw):
        """Create a relationship cert in the store, return (cert_id, peer_did)."""
        from proxion_messenger_core.federation import RelationshipCertificate, Capability
        from proxion_messenger_core.didkey import pub_key_to_did
        peer_priv = Ed25519PrivateKey.generate()
        peer_pub = peer_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        peer_pub_hex = peer_pub.hex()
        peer_did = pub_key_to_did(peer_pub)
        owner_pub_hex = gw.agent.identity_pub_bytes.hex()
        cert = RelationshipCertificate(
            issuer=owner_pub_hex,
            subject=peer_pub_hex,
            capabilities=[Capability(with_="stash://dm/", can="crud/write")],
        )
        cert.sign(gw.agent.identity_key)
        if gw._store:
            gw._store.save_relationship(cert.to_dict(), peer_did=peer_did)
        return cert.certificate_id, peer_did

    # The non-participant invariant is only meaningful (and enforceable) when auth
    # is enforced: then _client_webids is a PROVEN identity (auth challenge /
    # delegation cert), so a caller whose DID is neither the owner/account DID nor
    # the peer is a genuine outsider. Under auth-off loopback the DID is an
    # unauthenticated self-claim — an "attacker" could just claim the owner DID,
    # and the real local user registers under a session DID ≠ the gateway DID — so
    # the check is gated off (see ProxionGateway._auth_enforced). These tests pin
    # the enforced-auth scenario (a network-exposed / multi-identity gateway).
    @pytest.mark.asyncio
    async def test_dm_reaction_unauthorized_attacker_blocked(self, tmp_path, monkeypatch):
        """Attacker who is not a DM participant cannot add reactions (auth enforced)."""
        from proxion_messenger_core.didkey import pub_key_to_did
        monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
        gw = _make_gateway(tmp_path)
        cert_id, peer_did = self._make_cert_and_rel(gw)

        # Attacker is not the owner or the peer
        attacker_ws = _fake_ws(gw, "did:key:zattacker")
        await gw._handle_add_reaction(attacker_ws, {
            "cert_id": cert_id,
            "message_id": "msg-001",
            "emoji": "💀",
        })
        responses = [json.loads(c.args[0]) for c in attacker_ws.send.call_args_list]
        assert any(r.get("type") == "error" and "participant" in r.get("message", "") for r in responses)

    @pytest.mark.asyncio
    async def test_dm_reaction_remove_unauthorized_attacker_blocked(self, tmp_path, monkeypatch):
        """Attacker cannot remove reactions from a DM they don't participate in (auth enforced)."""
        monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
        gw = _make_gateway(tmp_path)
        cert_id, peer_did = self._make_cert_and_rel(gw)

        attacker_ws = _fake_ws(gw, "did:key:zattacker2")
        await gw._handle_remove_reaction(attacker_ws, {
            "cert_id": cert_id,
            "message_id": "msg-002",
            "emoji": "👍",
        })
        responses = [json.loads(c.args[0]) for c in attacker_ws.send.call_args_list]
        assert any(r.get("type") == "error" and "participant" in r.get("message", "") for r in responses)

    @pytest.mark.asyncio
    async def test_dm_reaction_authorized_owner_allowed(self, tmp_path, monkeypatch):
        """The gateway owner (DM participant) can add reactions (even with auth enforced,
        the owner's account DID == the gateway DID, so the participant check passes)."""
        from proxion_messenger_core.didkey import pub_key_to_did
        monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
        gw = _make_gateway(tmp_path)
        cert_id, peer_did = self._make_cert_and_rel(gw)
        owner_did = pub_key_to_did(gw.agent.identity_pub_bytes)

        owner_ws = _fake_ws(gw, owner_did)
        await gw._handle_add_reaction(owner_ws, {
            "cert_id": cert_id,
            "message_id": "msg-003",
            "emoji": "❤️",
        })
        responses = [json.loads(c.args[0]) for c in owner_ws.send.call_args_list]
        # Should not receive an error about participation
        assert not any(r.get("type") == "error" and "participant" in r.get("message", "") for r in responses)

    @pytest.mark.asyncio
    async def test_dm_reaction_authorized_peer_allowed(self, tmp_path):
        """The DM peer (other participant) can add reactions."""
        from proxion_messenger_core.didkey import pub_key_to_did
        gw = _make_gateway(tmp_path)
        cert_id, peer_did = self._make_cert_and_rel(gw)

        peer_ws = _fake_ws(gw, peer_did)
        await gw._handle_add_reaction(peer_ws, {
            "cert_id": cert_id,
            "message_id": "msg-004",
            "emoji": "🔥",
        })
        responses = [json.loads(c.args[0]) for c in peer_ws.send.call_args_list]
        assert not any(r.get("type") == "error" and "participant" in r.get("message", "") for r in responses)


# ---------------------------------------------------------------------------
# Finding 3: Room ownership hijacking on creator departure
# ---------------------------------------------------------------------------

class TestRoomOwnershipPromotion:
    @pytest.mark.asyncio
    async def test_room_leave_promotes_admin_over_member(self, tmp_path):
        """When creator leaves, an existing admin is promoted over regular members."""
        gw = _make_gateway(tmp_path)
        room_id = "room-roles"
        creator_did = "did:key:zcreator"
        admin_did = "did:key:zadmin"
        member_did = "did:key:zplainmember"

        creator_ws = _fake_ws(gw, creator_did)
        admin_ws = _fake_ws(gw, admin_did)
        member_ws = _fake_ws(gw, member_did)

        _add_room(gw, room_id, {creator_ws, admin_ws, member_ws}, creator_webid=creator_did)
        if gw._store:
            gw._store.save_room(room_id, "Roles", "code-roles", "", "none", creator_did)
            gw._store.add_room_member(room_id, creator_did)
            gw._store.add_room_member(room_id, admin_did)
            gw._store.add_room_member(room_id, member_did)
            # Grant admin role to admin_did; member_did stays as default "member"
            gw._store.set_room_role(room_id, admin_did, "admin")

        # Creator leaves
        await gw._handle_leave_local_room(creator_ws, {"room_id": room_id})

        # Room should still exist and admin_did should be the new creator
        assert room_id in gw._local_rooms, "Room should still exist with remaining members"
        new_creator = gw._local_rooms[room_id].get("creator_webid")
        assert new_creator == admin_did, (
            f"Admin should be promoted, got {new_creator!r}"
        )

    @pytest.mark.asyncio
    async def test_room_leave_promotes_mod_over_member(self, tmp_path):
        """When creator leaves, a mod is promoted over a regular member."""
        gw = _make_gateway(tmp_path)
        room_id = "room-mod"
        creator_did = "did:key:zcreator2"
        mod_did = "did:key:zmod"
        member_did = "did:key:zplain"

        creator_ws = _fake_ws(gw, creator_did)
        mod_ws = _fake_ws(gw, mod_did)
        member_ws = _fake_ws(gw, member_did)

        _add_room(gw, room_id, {creator_ws, mod_ws, member_ws}, creator_webid=creator_did)
        if gw._store:
            gw._store.save_room(room_id, "Mod", "code-mod", "", "none", creator_did)
            gw._store.add_room_member(room_id, creator_did)
            gw._store.add_room_member(room_id, mod_did)
            gw._store.add_room_member(room_id, member_did)
            gw._store.set_room_role(room_id, mod_did, "mod")

        await gw._handle_leave_local_room(creator_ws, {"room_id": room_id})

        assert room_id in gw._local_rooms
        new_creator = gw._local_rooms[room_id].get("creator_webid")
        assert new_creator == mod_did, f"Mod should be promoted, got {new_creator!r}"

    @pytest.mark.asyncio
    async def test_room_leave_no_elevated_roles_picks_any_member(self, tmp_path):
        """When no admin/mod exists, any remaining member is promoted (existing behaviour)."""
        gw = _make_gateway(tmp_path)
        room_id = "room-flat"
        creator_did = "did:key:zcreator3"
        member_did = "did:key:zflat"

        creator_ws = _fake_ws(gw, creator_did)
        member_ws = _fake_ws(gw, member_did)

        _add_room(gw, room_id, {creator_ws, member_ws}, creator_webid=creator_did)
        if gw._store:
            gw._store.save_room(room_id, "Flat", "code-flat", "", "none", creator_did)
            gw._store.add_room_member(room_id, creator_did)
            gw._store.add_room_member(room_id, member_did)

        await gw._handle_leave_local_room(creator_ws, {"room_id": room_id})

        assert room_id in gw._local_rooms
        new_creator = gw._local_rooms[room_id].get("creator_webid")
        assert new_creator == member_did, f"Remaining member should become owner, got {new_creator!r}"
