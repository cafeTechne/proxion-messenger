# Security model: interop and calls

This describes the trust boundaries added by the cross-app interop features (chat
discovery, invitations, notifications) and by calls, the attacker each one assumes, and
the mitigation. It complements `docs/CALLS.md` and `docs/NOTIFICATIONS.md`.

## Principles

- The gateway relays and coordinates; it is not trusted with message or media content.
  Direct messages are end-to-end encrypted, and 1:1 call media is DTLS-SRTP encrypted
  between the peers.
- Data written to a pod is protected by that pod's ACLs. Proxion grants the narrowest
  access that makes a feature work and no more.
- Anything read from another pod or another app is untrusted input. It is parsed
  defensively and rendered as text, never as markup.

## The Solid inbox (Linked Data Notifications)

The inbox is public-Append: any agent may drop a notification, and only the owner can
read, list, or delete. That is the LDN norm and it is what lets any Solid app invite you.

- **Flooding.** Because anyone can write, an inbox can be flooded. The client processes
  at most a fixed number of notifications per read (100), so a flood cannot hang the app
  or trigger an unbounded number of fetches. Pruning the inbox is the pod operator's
  lever.
- **Hostile notifications.** Notification JSON is attacker-controlled. The parser reads a
  bounded, non-recursive shape and only surfaces entries that reference a chat container;
  anything else is ignored. Sender WebIDs and titles reach the DOM only as text.
- **Accepting an invite.** Accept only ever runs the access-checked join. A spoofed
  invite to a chat you were never granted simply fails to open. There is no auto-join.

## Closed-app push

When the gateway is publicly reachable, the pod's server calls a gateway webhook on
inbox change; the gateway relays a content-free Web Push.

- **Webhook token.** The token is an HMAC over the WebID keyed by the gateway's VAPID
  key, compared in constant time. A token for one WebID cannot push another, and a
  missing or altered token pushes nothing.
- **Open endpoint.** The webhook path is reachable by anyone, but without a valid token
  it does nothing, the request body is never parsed or trusted, and the push it can
  cause carries only a type, no sender or content. Pushes to one WebID are rate-limited,
  so a flood collapses to one notification.

## Behind-NAT poll

When the gateway is not reachable, it can instead poll a user's inbox over an outbound
connection and push on change. This needs the user to grant the gateway's WebID read
access to their inbox.

- **Delegation scope.** The grant is read-only and inbox-scoped. Owner control and the
  public-Append rule are preserved; the gateway never receives write or broader read.
- **No SSRF.** The gateway only fetches an inbox that is same-origin as the WebID, so a
  profile cannot point the gateway's authenticated fetch at an internal or attacker host.
- **Bounded state.** The gateway tracks only an inbox's current notifications, not every
  one ever seen, so long-running memory stays bounded. The first poll seeds silently, so
  pre-existing invitations do not fire a spurious push.

## Calls

1:1 call media is DTLS-SRTP encrypted end to end; the gateway relays only signaling and a
TURN relay sees only ciphertext. See `docs/CALLS.md` for the full description.

- **Media authentication.** Each peer signs its DTLS fingerprint with its Ed25519
  identity key, bound to the call session and role. The other peer verifies it against
  the contact's known identity and checks it against every fingerprint in the SDP it
  received. A relay that swaps or splits the fingerprint is detected and the call is
  refused. A known contact whose signature is stripped is refused, not silently
  downgraded.
- **Scope.** This covers 1:1 and group calls. A group call is a mesh of peer
  connections, and each pair is authenticated the same way against the co-member's
  known identity.
- **Capture.** Camera and screen capture require an explicit action and the browser's
  permission prompt. A self-view and an on-air indicator show what is being sent.
  Nothing is recorded.
