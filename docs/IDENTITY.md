# Identity model

Proxion has three kinds of identity. They exist for good reasons, but they are easy to
confuse, and confusing them has caused real bugs (a reply lost after a cross-gateway
receive, a relay that failed authorization, a call that could not verify a contact). This
document is the contract: what each identity is, which one is authoritative for "who a
contact is," how they relate, and which one every wire field carries. New code that deals
with identity should conform to it, and route its lookups through the resolver described
at the end rather than reconciling identities by hand.

## The three identities

### Account did
A person's stable messaging identity, a `did:key`. This is the answer to "who is this
contact." A relationship (friendship) is recorded against an account did, and it is what
`peer_did` / `peerDidToCertId` hold on the client. When you verify who you are talking to,
you verify against an account did.

In practice a user's account identity is their gateway's owner identity
(`pub_key_to_did(agent.identity_pub_bytes)`), because relationships are established
gateway to gateway during the federation handshake. So for a federated contact, "account
did" and "their gateway did" are the same key. This is why the account did is the
authoritative contact identity.

### Gateway did
A gateway's own identity (`pub_key_to_did(agent.identity_pub_bytes)`), a `did:key`. It
signs relay envelopes (a relayed message, DM, or voice signal carries `from_webid` and
`relay_sig_did` equal to the gateway did) and it is the key by which one gateway routes to
another (`_record_peer_gateway`, `_resolve_peer_gateway` map a gateway did to a URL).

A gateway did is a routing and transport identity, not a person. Two facts follow, and
both have bitten us:
- **Routing uses the gateway did.** Answering or ICE-routing a cross-gateway call back to
  a peer must target a gateway-routable identity, not the peer's browser key.
- **The gateway did equals the contact's account did** (see above), so it is also what
  you verify a federated contact against.

### Device did
The key a running client actually signs with, a `did:key` generated in the browser
(`clientDid`, stored as `proxion_identity_did`). It signs the auth challenge on connect,
is the per-device id for DM fanout, and signs a call's DTLS fingerprint. On a single
device the device did and the account did are the same key. On a device linked to an
existing account they differ, and a delegation cert bridges them.

## How they relate: reduction

The core operation is **reduction**: given any identity seen on the wire, determine the
account (contact) it belongs to.

- A device did reduces to its account did via a **device certificate**
  (`device_cert.py` / `device-cert.js`): `account_did` signs a cert naming a
  `device_did`, and `verify_device_cert` returns the account for a valid, unexpired cert.
  Two delegations use this same primitive:
  - **Linked device to account.** A primary device certifies a secondary device (the
    multi-device delegation cert), so both act for one account.
  - **Browser to gateway.** On register, the gateway certifies the connecting browser's
    signing key as speaking for the gateway identity (issued in the `registered`
    message, cached client-side as `proxion_gateway_delegation_cert`). This lets a call
    signed by the browser key be bound to the gateway identity a federated contact knows,
    so verification works across gateways. See [CALLS.md](CALLS.md).
- An account did reduces to itself.
- A gateway did, for a contact, is that contact's account did (they coincide), so it also
  reduces to the contact.

A signature is **bound** to a contact when the signer is the contact's account did, or a
device cert chains the signer to it. Binding, not mere presence of a signature, is what
lets a receiver treat a signer as the contact.

## What each wire field carries

| Field | Identity | Purpose |
|-------|----------|---------|
| register `did` | device did (browser) | the key this connection signs with |
| `_client_webids[ws]` | account did | who this connection acts as (account for a linked device, else the device did) |
| relay `from_webid`, `relay_sig_did` | gateway did | envelope signer and router |
| voice `caller_webid` (in signal data) | account did | the caller's identity, for verification |
| voice `from_webid` (relayed event) | gateway did | the relaying gateway, for routing the reply |
| call `fp_signer` | device did | who signed the DTLS fingerprint |
| call `fp_cert` | device to gateway/account cert | reduces `fp_signer` to the contact |
| relationship `peer_did` | account did | the contact this relationship is with |

The recurring mistake is reading one of these as another: routing a call answer by the
account/device did instead of the gateway did (the reply never arrives), or verifying a
call against the gateway did while it was signed by the browser did with no cert to bridge
them (a legitimate call cannot verify). Keep routing on the gateway did and verification
on the account did, and bridge the device did to the account with a cert.

## The resolver

To stop each call site from reconciling these by hand, identity questions go through a
single resolver (`web/identity.js`, `createIdentityResolver`). It answers:

- **Who am I?** `selfDeviceDid()` (the key I sign with) and `selfAccountDid()` (who others
  know me as).
- **What contact does this reduce to?** `contactForCall(view, event)` maps an identity on
  a call (an event's `caller_webid`/`from_webid`, or the open thread's peer) to the
  account did of a known contact, or `''` when unknown.

Call identity verification consumes the resolver today. The plan is to move DM fanout,
mute keys, contact resolution, and the gateway-side authorization checks
(`get_relationship_by_did`, envelope authz, voice routing) onto the same reduction, so
there is one definition of "who is this" rather than one per surface.
