# Calls: voice, video, screen sharing

Proxion supports 1:1 voice and video calls and screen sharing, plus group voice
channels. This document describes how calls work and what makes them private.

## How the media flows

Calls use WebRTC. The two devices connect peer to peer and exchange audio and video
directly. The gateway relays only the signaling (the SDP offer/answer and ICE
candidates that let the peers find each other); it never carries the media. When a
direct path is blocked by NAT, a TURN relay forwards the packets, but those packets
are already encrypted, so the relay sees ciphertext, not your call.

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

- If the signature is valid and the fingerprint matches, the call shows **Verified**
  and you have cryptographic proof the media channel is with the real contact.
- If a relay altered the fingerprint, or the identity does not match the expected
  contact, the call is **refused**.
- If the peer is not a known contact (so there is no identity to check against), the
  call still connects, is still DTLS-SRTP encrypted, and is shown as **Unverified**.

The gateway cannot forge a contact's Ed25519 signature, so it cannot insert itself
into a verified call without detection.

## Privacy of capture

- The camera and screen are never captured without an explicit action from you, and
  the browser's own permission prompt gates every capture.
- A self-view shows exactly what your camera is sending, and the screen picker shows
  what you are about to share.
- While your camera or screen is live, the call widget shows an indicator, so you
  always know what is being sent.
- Nothing is recorded. Media streams exist only for the duration of the call.

## Scope

Video and screen sharing are 1:1 today. Group calls are voice. There is no media
server: calls are peer to peer, which is what keeps the media out of any middle box.
