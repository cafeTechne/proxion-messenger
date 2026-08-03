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

- If the signature is valid and matches the identity you know the contact by, the call
  shows **Verified**, and you have cryptographic proof the media channel is with them.
- Otherwise the call shows **Unverified**. It still connects and is still DTLS-SRTP
  encrypted; you simply do not have the extra identity proof.

This is advisory, not blocking: the check surfaces a status, it does not refuse the
call. The reason is that a call is signed by the browser's identity key, while a
federated contact is known to you by their gateway identity, so the two do not always
line up even for a legitimate call. Binding the call signature to the contact roster
(so Verified can be shown across gateways, and a genuine mismatch can be treated as an
alarm) is planned work. The media is end-to-end encrypted regardless.

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
