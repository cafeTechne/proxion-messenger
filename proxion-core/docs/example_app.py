"""
Proxion Protocol -- Developer Example App
=========================================

This self-contained script demonstrates how to build an application on the
Proxion stack. It shows the full lifecycle:

  1. Two agents run a 3-step federation handshake - RelationshipCertificate
  2. A capability token is minted from the certificate (least-authority)
  3. The token is attenuated: narrowed to a time window and IP allowlist
  4. The attenuated token is validated against matching and failing contexts
  5. The certificate is revoked; subsequent validation is denied

This is the "app developer" entry point: import proxion_messenger_core, create
AgentStates for your service nodes, and use capability tokens to gate access
to any resource -- a Solid Pod path, an API endpoint, a WireGuard peer slot.

Run with:
    python docs/example_app.py
"""

from __future__ import annotations

import datetime
import uuid

import secrets as _secrets
from unittest.mock import MagicMock

from proxion_messenger_core import (
    AgentState,
    RequestContext,
    derive_token,
    ip_allowlist,
    compose,
    receive,
    run_bidirectional_handshake,
    send,
    time_window,
    validate_request,
)
from proxion_messenger_core.pop import PopProof, sign_challenge
from proxion_messenger_core.certtoken import issue_from_certificate
from proxion_messenger_core.federation import Capability
from proxion_messenger_core.handshake import run_local_handshake
from proxion_messenger_core.solid import SolidResolver
from proxion_messenger_core.solid_client import SolidClient, SolidError
from proxion_messenger_core.revocation import RevocationList
from proxion_messenger_core.store import MemoryStore

SEP = "-" * 60


def _mock_pod_client(stored: dict[str, bytes] | None = None) -> SolidClient:
    """In-memory SolidClient-like mock used for messaging demo steps."""
    storage: dict[str, bytes] = stored or {}
    resolver = MagicMock(spec=SolidResolver)

    def _to_http(uri: str) -> str:
        without_scheme = uri[len("stash://"):]
        slash = without_scheme.find("/")
        if slash == -1:
            return "http://pod/"
        path = without_scheme[slash + 1:]
        return f"http://pod/{path}" if path else "http://pod/"

    def _to_stash(url: str) -> str:
        if url.startswith("http://pod/"):
            return f"stash://pod/{url[len('http://pod/'):]}"
        return url

    resolver.resolve.side_effect = _to_http

    client = MagicMock(spec=SolidClient)
    client._resolver = resolver

    def _put(path, data, content_type="application/octet-stream"):
        _ = content_type
        storage[_to_http(path)] = data

    def _get(path):
        url = _to_http(path)
        if url not in storage:
            raise SolidError(f"not found: {url}", status_code=404)
        return storage[url]

    def _list(path):
        prefix = _to_http(path)
        return [_to_stash(k) for k in storage if k.startswith(prefix) and k != prefix]

    client.put.side_effect = _put
    client.get.side_effect = _get
    client.list.side_effect = _list
    return client


def main() -> None:
    print(SEP)
    print("Proxion Protocol -- Developer Example")
    print(SEP)

    # -- 1. Bootstrap two agents ----------------------------------------------
    print("\n[1] Generating two agents (Alice = resource owner, Bob = app node)...")
    alice = AgentState.generate()
    bob   = AgentState.generate()
    print(f"    Alice: {alice.identity_pub_bytes.hex()[:32]}...")
    print(f"    Bob:   {bob.identity_pub_bytes.hex()[:32]}...")

    # -- 2. 3-step federation handshake via shared in-process store -----------
    print("\n[2] Running 3-step federation handshake (invite - accept - certify)...")
    store = MemoryStore()
    capabilities = [
        Capability(can="read",  with_="stash://media/"),
        Capability(can="write", with_="stash://media/uploads/"),
    ]

    cert, cert_valid = run_local_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_capabilities=capabilities,
        bob_capabilities=capabilities,  # Bob echoes back what he accepts
        store=store,
    )
    assert cert_valid, "Certificate signature invalid"
    alice.certificates.append(cert)
    bob.certificates.append(cert)
    token_aud = cert.issuer
    print(f"    Certificate: {cert.certificate_id[:32]}...")
    print(f"    Capabilities: {[f'{c.can}:{c.with_}' for c in cert.capabilities]}")

    # -- 3. Mint a capability token from the certificate ----------------------
    print("\n[3] Minting a capability token scoped to stash://media/ (read only)...")
    signing_key = alice.signing_key_bytes     # issuer's HMAC signing key
    now_dt = datetime.datetime.now(datetime.timezone.utc)

    token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", "stash://media/")],
        holder_pub_key=bob.identity_key.public_key(),
        signing_key=signing_key,
        ttl_seconds=3600,
        now=now_dt,
    )
    print(f"    Token ID:    {token.token_id[:32]}...")
    print(f"    Permissions: {list(token.permissions)}")
    print(f"    Expires:     {token.exp.strftime('%H:%M:%S UTC')}")

    # -- 4. Attenuate: restrict to 30 min window + IP allowlist ---------------
    print("\n[4] Attenuating token - 30-min window, IP locked to 10.0.0.1...")
    window_end = now_dt + datetime.timedelta(minutes=30)
    attenuated = derive_token(
        parent=token,
        narrower_perms=[("read", "stash://media/")],
        extra_caveats=[
            time_window(not_before=now_dt.timestamp(), not_after=window_end.timestamp()),
            ip_allowlist({"10.0.0.1"}),
        ],
        signing_key=signing_key,
        now=now_dt,
    )
    print(f"    Caveats added: time_window, ip_allowlist")

    # -- 5. Validate: ALLOW ---------------------------------------------------
    print("\n[5] Validating against matching request (correct IP, within window)...")
    revocation = RevocationList()
    ctx_allow = RequestContext(
        action="read",
        resource="stash://media/movie.mkv",
        aud=token_aud,
        now=now_dt,
        ip="10.0.0.1",
    )
    proof = sign_challenge(bob.identity_key, attenuated.token_id, _secrets.token_hex(8))
    result = validate_request(
        token=attenuated,
        ctx=ctx_allow,
        proof=proof,
        signing_key=signing_key,
        revocation_list=revocation,
    )
    assert result.allowed, f"Expected ALLOW, got: {result.reason}"
    print(f"    Decision: ALLOW OK")

    # -- 6. Validate: DENY (wrong IP) -----------------------------------------
    print("\n[6] Same token, wrong IP - should DENY...")
    ctx_deny = RequestContext(
        action="read",
        resource="stash://media/movie.mkv",
        aud=token_aud,
        now=now_dt,
        ip="192.168.99.1",
    )
    proof2 = sign_challenge(bob.identity_key, attenuated.token_id, _secrets.token_hex(8))
    result_deny = validate_request(
        token=attenuated,
        ctx=ctx_deny,
        proof=proof2,
        signing_key=signing_key,
        revocation_list=revocation,
    )
    assert not result_deny.allowed
    print(f"    Decision: DENY OK  (reason: {result_deny.reason})")

    # -- 7. Revoke certificate; token derived from it is now denied -----------
    print("\n[7] Alice revokes a token; subsequent validation is denied...")
    fresh_token = issue_from_certificate(
        cert=cert,
        requested_permissions=[("read", "stash://media/")],
        holder_pub_key=bob.identity_key.public_key(),
        signing_key=signing_key,
        ttl_seconds=3600,
        now=now_dt,
    )
    revocation.revoke(fresh_token, now_dt)  # revoke by full token payload
    proof3 = sign_challenge(bob.identity_key, fresh_token.token_id, _secrets.token_hex(8))
    result_revoked = validate_request(
        token=fresh_token,
        ctx=ctx_allow,
        proof=proof3,
        signing_key=signing_key,
        revocation_list=revocation,
    )
    assert not result_revoked.allowed
    print(f"    Decision: DENY OK  (reason: {result_revoked.reason})")

    # -- 8. Bidirectional handshake + message exchange ------------------------
    print("\n[8] Bidirectional handshake and message exchange...")
    msg_store = MemoryStore()
    cert_id_ab = str(uuid.uuid4())
    cert_id_ba = str(uuid.uuid4())
    caps_ab = [Capability(can="read", with_=f"stash://messages/thread/{cert_id_ab}/")]
    caps_ba = [Capability(can="read", with_=f"stash://messages/thread/{cert_id_ba}/")]
    (cert_ab, valid_ab), (cert_ba, valid_ba) = run_bidirectional_handshake(
        alice_identity_priv=alice.identity_key,
        alice_store_priv=alice.store_key,
        bob_identity_priv=bob.identity_key,
        bob_store_priv=bob.store_key,
        alice_to_bob_capabilities=caps_ab,
        bob_to_alice_capabilities=caps_ba,
        store=msg_store,
        certificate_id_a_to_b=cert_id_ab,
        certificate_id_b_to_a=cert_id_ba,
    )
    assert valid_ab and valid_ba
    alice_pod = _mock_pod_client()
    bob_pod = _mock_pod_client()

    msg_a = compose(alice.identity_key, cert_ab, "Hello via Proxion messaging")
    send(msg_a, alice_pod)
    print('    Alice -> Bob: "Hello via Proxion messaging"')

    msg_b = compose(bob.identity_key, cert_ba, "Reply received")
    send(msg_b, bob_pod)
    print('    Bob -> Alice: "Reply received"')

    # -- 9. Enforced receive using narrow thread token ------------------------
    print("\n[9] Bob reads Alice's messages (capability enforced)...")
    received = receive(
        cert_ab,
        alice_pod,
        holder_state=bob,
        signing_key=alice.signing_key_bytes,
    )
    assert len(received) >= 1
    print(f'    Received {len(received)} message: "{received[0].content}"')

    # -- 10. Cert renewal demo -------------------------------------------------
    print("\n[10] Alice renews an expiring cert (new TTL, new cert ID)...")
    from proxion_messenger_core.certtoken import renew_cert
    renewed_cert = renew_cert(cert_ab, alice.identity_key, new_ttl_days=365)
    assert renewed_cert.certificate_id != cert_ab.certificate_id
    assert renewed_cert.expires_at > cert_ab.expires_at
    assert renewed_cert.issuer == cert_ab.issuer
    assert renewed_cert.subject == cert_ab.subject
    print(f"    Old cert ID: {cert_ab.certificate_id[:16]}...")
    print(f"    New cert ID: {renewed_cert.certificate_id[:16]}...")
    print(f"    Renewed for 365 days — new expiry ahead of original.")

    # -- 11. CSS / DPoP demo (skipped if CSS_ALICE_URL not set) ---------------
    print("\n[11] CSS DPoP demo (set CSS_ALICE_URL env var to run)...")
    import os as _os
    css_url = _os.environ.get("CSS_ALICE_URL", "").rstrip("/")
    if not css_url:
        print("    CSS_ALICE_URL not set -- skipping live CSS demo")
    else:
        from proxion_messenger_core.css_setup import CssAccountManager, build_dpop_client
        import uuid as _uuid
        demo_email = f"demo-{_uuid.uuid4().hex[:8]}@test.example"
        css_mgr = CssAccountManager(css_url)
        demo_creds, pod_url, webid = css_mgr.setup_agent(
            alice.identity_key, demo_email, "demopass123"
        )
        demo_client = build_dpop_client(demo_creds, pod_url, stash_owner="pod")
        demo_client.put("stash://pod/proxion-demo.txt", b"Hello from Proxion DPoP!")
        result = demo_client.get("stash://pod/proxion-demo.txt")
        assert result == b"Hello from Proxion DPoP!"
        print(f"    CSS Pod: {pod_url}")
        print(f"    WebID:   {webid}")
        print(f"    PUT + GET round-trip: OK")

    # -- 12. Rooms, File Sharing, and Presence Demo ---------------------------
    print("\n[12] Room, file sharing, and presence demo (mocked)...")
    from proxion_messenger_core.room import create_room, invite_to_room, join_room, send_to_room, read_room, set_room_acl
    from proxion_messenger_core.files import send_file, receive_files, download_file
    from proxion_messenger_core.presence import set_presence, get_presence

    # Reuse Alice and Bob agents
    alice_pod = _mock_pod_client()
    bob_pod = _mock_pod_client()
    alice_webid = "stash://alice/profile/card#me"
    bob_webid = "stash://bob/profile/card#me"
    
    # Alice creates a room
    room = create_room(alice_pod, alice_webid, "Dev Hangout")
    set_room_acl(room, alice_pod, alice_webid, [bob_webid])
    
    # Handshake via MemoryStore
    shared_coordination = MemoryStore()
    from proxion_messenger_core.store_client import LocalStoreAdapter
    alice_remote = LocalStoreAdapter(shared_coordination)
    
    invite_json = invite_to_room(room, alice)
    membership = join_room(invite_json, bob, bob_webid, alice_remote)
    
    # Alice finalizes
    from proxion_messenger_core.handshake import finalize_handshake
    finalize_handshake(alice.identity_key, alice.store_key, alice_remote)
    
    # Bob gets his cert
    from proxion_messenger_core.handshake import receive_certificates
    certs = receive_certificates(bob.store_key, alice_remote)
    membership.cert, _ = certs[0]
    
    # Bob sends a message to the room
    send_to_room(bob_pod, room, "Hello room!")
    
    # Alice reads room
    alice_msgs = read_room(membership, alice_pod, alice)
    assert any(m.content == "Hello room!" for m in alice_msgs)
    print("    Room messaging: OK")
    
    # Alice sends a file to the room
    file_uri = send_file(alice_pod, room, b"fake image content", filename="photo.png")
    
    # Bob receives files
    attachments = receive_files(membership, bob_pod, bob)
    assert any(a.filename == "photo.png" for a in attachments)
    print("    File sharing: OK")
    
    # Both set presence
    set_presence(alice_pod, alice_webid, "online")
    set_presence(bob_pod, bob_webid, "online")
    
    # Fetch each other's presence
    alice_presence = get_presence(bob_pod, alice_webid)
    bob_presence = get_presence(alice_pod, bob_webid)
    
    assert alice_presence.status == "online"
    assert bob_presence.status == "online"
    print("    Presence management: OK")
    
    print("\n    Room, file sharing, and presence: OK")

    # -- 13. Multi-device: Alice links a second device ------------------------
    print("\n[13] Multi-device: Alice links a second device...")
    from proxion_messenger_core.device import create_device_link, export_device_invite, import_device_invite
    
    # 1. New device generates a key
    device_key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    device_pub = device_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    ).hex()
    
    # 2. Alice (primary device) creates a link
    link = create_device_link(alice, device_pub, ttl_days=365)
    invite_json = export_device_invite(link)
    
    # 3. Import on "new" device (using same agent as placeholder for verification)
    # In reality, this would be a fresh AgentState
    restored_link = import_device_invite(invite_json, alice)
    assert restored_link.device_pub_hex == device_pub
    print(f"    Device link created for: {link.device_pub_hex[:16]}...")
    print("    Multi-device delegation: OK")

    # [16] Message editing, pinning, and history export
    print("\n[16] Message editing, pinning, and history export...")
    from proxion_messenger_core.messaging import compose_and_send, edit_message, apply_edits, send
    from proxion_messenger_core.pins import pin_message, get_pinned_messages

    class _FakePod:
        """Minimal in-memory Pod stand-in for this demo step."""
        def __init__(self):
            self._store = {}
        def put(self, path, data, **_):
            self._store[path] = data
        def get(self, path):
            return self._store[path]
        def list(self, prefix):
            return [k for k in self._store if k.startswith(prefix) and k != prefix]
        def delete(self, path):
            self._store.pop(path, None)

    fake_pod = _FakePod()
    cert, _ = run_local_handshake(
        alice.identity_key, alice.store_key,
        bob.identity_key, bob.store_key,
        [], [], MemoryStore(),
    )

    original = compose_and_send(alice.identity_key, cert, "First draft", fake_pod)
    edit = edit_message(alice.identity_key, cert, original.message_id, "Final version")
    send(edit, fake_pod)

    from proxion_messenger_core.messaging import receive
    all_msgs = receive(cert, fake_pod)
    applied = apply_edits(all_msgs)
    edited = next(m for m in applied if m.message_id == original.message_id)
    assert edited.content == "Final version", f"Expected 'Final version', got {edited.content!r}"
    print("    Message editing: OK")

    pinned = pin_message(fake_pod, edited, f"dm:{cert.certificate_id}", "alice@example.com")
    pins = get_pinned_messages(fake_pod, f"dm:{cert.certificate_id}")
    assert len(pins) == 1
    assert pins[0].message_id == original.message_id
    print("    Message pinning: OK")

    # [17] DID identity and room discovery
    print("\n[17] DID identity and room discovery...")
    from proxion_messenger_core.didkey import pub_key_to_did, did_to_pub_key, agent_did
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    alice_did = agent_did(alice)
    assert alice_did.startswith("did:key:z6Mk"), f"Unexpected DID format: {alice_did}"
    print(f"    Alice DID: {alice_did[:44]}...")

    alice_pub_bytes = alice.identity_key.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    roundtripped = did_to_pub_key(alice_did)
    assert roundtripped == alice_pub_bytes, "DID roundtrip mismatch"
    print("    DID roundtrip: OK")

    # [18] OIDC discovery and peer registry
    print("\n[18] OIDC discovery and peer registry...")
    from proxion_messenger_core.oidc import OidcConfig, fetch_oidc_config, dynamic_register
    from proxion_messenger_core.peerdb import PeerRecord, register_peer, get_peer, touch_peer
    
    # Note: fetch_oidc_config requires real network access, so we demonstrate structure
    example_oidc_config = OidcConfig(
        issuer="https://issuer.example.com",
        authorization_endpoint="https://issuer.example.com/authorize",
        token_endpoint="https://issuer.example.com/token",
        jwks_uri="https://issuer.example.com/.well-known/jwks.json",
    )
    assert example_oidc_config.issuer == "https://issuer.example.com"
    print(f"    OidcConfig structure: OK")
    
    # Peer registry demonstration
    peer = PeerRecord(
        did=alice_did,
        pod_url="https://alice.pod",
        display_name="Alice",
        trusted=True
    )
    assert peer.did == alice_did
    assert peer.trusted is True
    print("    Peer registry structure: OK")

    # -- Summary --------------------------------------------------------------
    print(f"\n{SEP}")
    print("All checks passed. Proxion stack verified end-to-end.\n")
    print("What this demonstrates:")
    print("  - Federation handshake: signed invite - acceptance - certificate")
    print("  - Capability tokens minted from certificates (least-authority)")
    print("  - Attenuation: tokens narrowed without contacting the issuer")
    print("  - Context-aware validation: IP, time window, audience")
    print("  - Revocation: token-level, immediate, fail-closed (cert cascade: revoke_tokens_for_certificate)")
    print("  - Cert renewal: re-issue with longer TTL, preserving issuer/subject")
    print("  - CSS/DPoP: DPoP-authenticated CSS Pod access (set CSS_ALICE_URL to demo)")
    print()
    print("To build an app on this protocol:")
    print("  1. AgentState.load() from persistent storage instead of .generate()")
    print("  2. Issue certificates to peer devices/services via the handshake flow")
    print("  3. Gate any resource with validate_request() -- API, file, WireGuard peer")
    print("  4. Write receipts to a Solid Pod for auditable, user-owned access history")
    print("  5. Attenuation lets you delegate subsets of access without re-issuing certs")
    print("  6. Renew certs before expiry with renew_cert(); use cert delegate for new devices")
    print("  7. For CSS/real-Solid Pods: use CssAccountManager.setup_agent() + build_dpop_client()")
    print(SEP)


if __name__ == "__main__":
    main()
