# Calls: voice, video, screen sharing

Proxion supports 1:1 voice and video calls and screen sharing, plus group voice
channels. This document describes how calls work and what makes them private.

## How the media flows

Calls use WebRTC. The two devices connect peer to peer and exchange audio and video
directly. The gateway relays only the signaling (the SDP offer/answer and ICE
candidates that let the peers find each other); it never carries the media. When a
direct path is blocked by NAT, a TURN relay forwards the packets, but those packets
are already encrypted, so the relay sees ciphertext, not your call.

## Connecting through restrictive networks

To connect peer to peer, each device has to discover an address the other can reach.
On ordinary home networks this works with STUN alone (built in, no setup), so most calls
just connect. Some networks (symmetric NAT, and many corporate or mobile/CGNAT firewalls)
allow no direct path at all. There the call needs a TURN relay: a server both sides can
reach that forwards the encrypted packets between them (the media stays end-to-end
encrypted, so a relay only ever carries ciphertext).

Proxion tries, in order:
1. **STUN** (a small set of public servers) for a direct path. This is enough for most
   calls.
2. **A free default public TURN relay**, so a call still connects on a restrictive network
   without anyone configuring anything. Proxion does not run this relay; see the runbook
   below. Users can disable it in Settings > Calls (privacy) or it can be turned off.
3. **A relay the user added** in Settings > Calls (their provider's TURN, or their own),
   and any relay a self-hosted gateway configures with `TURN_URL` + `TURN_SECRET` (coturn
   shared secret; `docker-compose.full.yml` bundles one, see [SELF_HOSTING.md](SELF_HOSTING.md)).

Settings > Calls also has **Test call connectivity**: it gathers ICE candidates and reports
whether the network is reachable and whether a relay is available, so a user can diagnose a
restrictive network before (or after) a call fails, and fix it by adding a relay.

### Maintainer runbook: the default public relay broke

The default public relay in step 2 is a **free, best-effort, third-party service** (like
the public STUN servers). It will eventually change endpoints, rate-limit, or shut down;
when it does, users behind restrictive NAT stop being able to connect calls.

- **Where it lives:** one constant, `DEFAULT_TURN`, in
  [`web/connectivity.js`](../web/connectivity.js). That file has the full inline runbook.
  Nothing else hard-codes a relay; everything reads `DEFAULT_TURN`.
- **How to tell it is the cause:** on a restrictive network, Settings > Calls > "Test call
  connectivity" reports "host only" instead of the "relay" verdict, even with the default
  relay enabled.
- **How to fix:** replace `DEFAULT_TURN` with another free/paid provider (Cloudflare TURN,
  Twilio, Xirsys, metered.ca) or a self-hosted coturn, then ship a release. Verify with the
  self-test (it should report the "relay" verdict on a restrictive network).
- **Meanwhile,** affected users can add their own relay in Settings > Calls without waiting
  for a release.

When a call still cannot find any path and no relay is reachable, it does not fail
silently: the caller is told the network is blocking a direct connection and a relay is
needed, which is the actual fix rather than a dead end.

## Encryption

WebRTC media is encrypted end to end by DTLS-SRTP. The peers derive the media keys
during the DTLS handshake that runs directly between them, so no server in the path,
the gateway or a TURN relay, ever holds the keys or sees plaintext audio or video.

## Authentication: defeating a tampered gateway

Encryption alone does not stop a malicious relay from sitting in the middle of the
handshake by swapping the DTLS fingerprint in the SDP it forwards. Proxion closes
that: each device signs the DTLS fingerprint from its own SDP with its Ed25519
identity key, and the other device verifies that signature against the contact's known
identity (their `did:key`) and checks that the signed fingerprint matches the one in
the SDP it actually received.

- If the signature is valid and binds to the identity you know the contact by, the call
  shows **Verified**, and you have cryptographic proof the media channel is with them.
- If the signer cannot be bound to that contact (for example an older client with no
  proof), the call shows **Unverified**. It still connects and is still DTLS-SRTP
  encrypted; you simply do not have the extra identity proof.
- If the signer IS your contact but the fingerprint signature does not check out, that
  is the fingerprint of a tampered media channel. The call is **refused**.

Binding across gateways. A call is signed by the browser's own key, while a federated
contact is known to you by their gateway identity (federation relationships are keyed
gateway to gateway). Proxion bridges the two: your gateway issues a short-lived
certificate binding your browser's signing key to its gateway identity, and the call
carries it, so the far side can tie the signature to the contact it already trusts and
show **Verified** even across gateways. A device linked to your account is bridged the
same way. When no such proof is present, the call is allowed but shown Unverified rather
than refused, so a legitimate call is never blocked; only a proven fingerprint swap or a
certificate that fails to chain is refused. The media is end-to-end encrypted regardless.

Downgrade protection. Once a contact is known to bind their calls, because we have already
accepted a bound (Verified) call from them, or because their relationship advertises it, a
later call from them that arrives with no binding proof is treated as a downgrade (a
stripped or withheld certificate) and refused, not quietly allowed. A contact we cannot
confirm is capable is still allowed as Unverified, so peers on older clients are never
blocked. This matters most when a gateway is shared or hosted rather than one you run
yourself; see PLAN_ROUND_86 for the threat boundary.

## Privacy of capture

- The camera and screen are never captured without an explicit action from you, and
  the browser's own permission prompt gates every capture.
- A self-view shows exactly what your camera is sending, and the screen picker shows
  what you are about to share.
- While your camera or screen is live, the call widget shows an indicator, so you
  always know what is being sent.
- Nothing is recorded. Media streams exist only for the duration of the call.

## Group calls

Group calls run as a mesh: each participant holds a direct peer connection to every
other, so there is no media server and the same DTLS-SRTP encryption and fingerprint
authentication apply to every pair. Camera and screen sharing work in a group; one
camera fans out to each peer.

Because mesh video bandwidth grows with the number of concurrent senders, the call
separates two limits. Audio is cheap and scales to a dozen or so participants. Video is
bounded not by call size but by how many people share at once: turning your camera on
past that cap is declined with a message, and audio is unaffected. Each video sender's
bitrate also scales down automatically as the call grows. The active speaker is
spotlighted so a larger call stays legible.

There is no media server and no recording: calls are peer to peer, which is what keeps
the media out of any middle box. Larger video rooms than a mesh can carry would need a
selective forwarding server, which would put media through a server and is deliberately
not part of Proxion.
