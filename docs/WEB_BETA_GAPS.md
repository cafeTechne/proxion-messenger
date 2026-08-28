# Proxion Web: beta gaps and bugs

A comprehensive audit of what stands between the deployed browser build
(`https://cafetechne.github.io/proxion-messenger/app/`) and a smooth beta. Written
after tracing the real signed-in flows and checking the live deploy. Grouped by
severity, with the fix for each. The app works as a website today; the items
below are the rough edges a tester will hit.

## Status (updated)

Sequenced and tracked in `PLAN_ROUND_106.md`. Resolved and deployed so far:
the silent DM loss (P0 item 1), the subpath PWA breakage (P1), click-to-copy
for your own WebID (P1 gap), and gateway-free room join (P1, the request/approve
handshake; a joiner reads and posts to the owner's room after approval, verified
against a live pod). Corrected: the connectivity/health block already has a
`.catch`, and pod-disconnect falls through to `logout()` on a 404, so the P2
"gateway-only endpoints" item is cosmetic (a blank federation panel and a wasted
request), not an error. The signed-in browser boot (P0 item 3) is now AUTOMATED:
`npm run smoke:web-signedin` completes a real OIDC login against a live CSS pod,
asserts the signed-in boot runs clean, then creates a room, posts a message, and
reloads, all with no page errors. It works headlessly by serving the app over
https with a self-signed cert (so OIDC accepts the client-id doc), letting CSS
trust that cert, and seeding CSS's account cookie to authorize the consent
screen.

The two-party room-join UI flow is now automated too: `npm run smoke:web-join`
signs in two accounts in isolated browser contexts, has the owner create a room
and share the invite, the joiner request to join, the owner approve the prompt,
and asserts the room appears for the joiner. Building it surfaced a real
cross-account delivery bug (below).

### Storage-root mismatch broke every cross-account drop (fixed)
Found by the two-party smoke. A sender derives a recipient's pod root from their
WebID path (`.../<account>/profile/card#me` -> `.../<account>/`), but the
recipient resolved their OWN root via `discoverStorageRoot()`, which bailed out
for non-https WebIDs and otherwise fell back to the bare origin (`.../`). On any
account-based server whose profile does not advertise `pim:storage`, the two
disagreed: the sender dropped into `.../<account>/proxion/<box>/` while the
recipient listened at `.../proxion/<box>/`. Every cross-account DM, call signal,
and join request missed, and two accounts on one server collided on a single
origin-root inbox. Fixed in `auth.js` by deriving the own-root from the WebID
path the same way the sender does, keeping an https `pim:storage` claim when the
profile provides one (including cross-origin, as Inrupt PodSpaces uses).

### Audit pass (three reviewers) — fixed and deferred

A focused review of the gateway-free surfaces (delivery, calls/presence, auth/ACL)
found more issues. Fixed in this round:
- **Fanout falsely reported delivery.** A DM's optimistic "pending" state cleared
  when a self-sync copy to one of the sender's OWN devices dropped, even if no
  copy reached the recipient. `dropFanout` now counts only drops to a non-self
  WebID as delivery, so an undelivered DM stays "Not delivered / Retry".
- **`voice_hangup` never reached the peer in web mode** (no `target_webid`, so the
  pod signaler dropped it): the callee's connection hung open until ICE failed.
  Hangup now carries the call peer.
- **Inbound ICE candidates were not buffered.** Over the pod, signals arrive
  unordered/batched, so a candidate landing before the remote description was set
  threw and was discarded, failing calls on NAT that need trickle. Candidates now
  buffer until the remote description is applied.
- **Storage-root cache was not bound to the WebID.** It is now trusted only when
  same-origin as the logged-in WebID, cleared on logout, and a cross-origin
  `pim:storage` is never persisted (re-derived each session). Stops a shared
  browser inheriting the previous account's root and blunts localStorage
  poisoning.
- **SSRF guard on the peer pod root.** A contact WebID resolving to a private /
  loopback / link-local host would have drawn authenticated (token-bearing)
  fetches to internal services. Every peer-pod request (DM/call/join drops,
  presence reads, E2E key discovery) now goes through `isPeerPodRootAllowed`
  (`ssrf.js`): public https peers are always allowed (cross-pod federation), a
  private host only when it is the user's own pod origin (so local/dev and
  self-hosted single-server still work). The host check covers IPv4 private
  ranges, IPv6 loopback/ULA/link-local, and IPv4-mapped IPv6.
- **Presence `start()` leaked on reconnect.** It stacked a second heartbeat timer
  and re-registered the window listeners each call, and `stop()` could not remove
  the anonymous listeners. `start()` is now idempotent, and `stop()` removes named
  listeners and re-enables a later `start()`.
- **Presence clock-skew.** Freshness was reader-clock minus the writer's
  self-reported heartbeat, so a skewed writer shifted online/away/offline (a
  crashed peer with a fast clock read "online" for the skew duration). Freshness
  now uses the presence resource's `Last-Modified` (a CORS-safelisted header, so
  one trusted server clock) when present, falling back to the heartbeat; the
  heartbeat is still shown as last-seen. (The unload offline write still cannot
  use `sendBeacon` — an authenticated pod PUT needs DPoP headers it cannot carry —
  and the freshness decay already covers a missed unload write.)
- **DM receive deleted the drop before a durable write.** The receive path fired
  the message event (which persists to the local DM history) fire-and-forget and
  then deleted the only ciphertext copy; the ratchet had already advanced, so a
  failed write meant permanent loss with no possible re-decrypt. `dmHistorySave`
  now reports success/failure, and the receive path persists (awaited) before
  deleting the drop, keeping the drop for a later retry when the write fails.
- **ACP servers: room-join grant (authored, pending live verification).**
  `podGrantChatParticipants` now branches on the discovered access-control model:
  on an ACP server it PUTs an ACR granting participants Read+Write+Append
  (`buildAcpAcr` gained a `memberModes` argument; the chat grant passes
  `acl:Read, acl:Write, acl:Append`), instead of WAC turtle that grants nothing
  there. The drop-box inboxes (DM/call/join) and the LDN inbox likewise author an
  ACR on an ACP server: owner full control, a public-Append policy via
  `acp:PublicAgent`, and any reader agents (the gateway poller) as Read, through a
  model-aware `_inboxAclBody` helper (`buildAcpAcr` gained a `publicModes`
  argument). All spec-authored and structurally unit-tested; the ACP path only
  activates when the server advertises ACP, so WAC servers (CSS) are unaffected.
  Still needs validation against a live Inrupt ESS (no ESS test account
  available), and the public type index is not yet ACP-authored.
- **Drop-box ACL write was not checked.** `_ensureDropBox` / `podEnsureInbox`
  PUT the public-Append ACL but ignored the response, so a 403/500 was treated as
  success and the box stayed owner-only (peers' drops 403'd invisibly). The ACL
  write now checks the response, retries once on failure, and logs clearly when
  the grant could not be placed. It stays best-effort (still returns the inbox so
  the owner is not blocked on their own resource by a transient hiccup) rather
  than hard-failing provisioning.
- Smaller: per-drop error isolation in the DM drain, a re-entrancy guard on the
  call-signal drain, and a guarded invite-token decode.

Remaining follow-ups need external resources, not code: live-verify the ACP paths
against an Inrupt ESS (no test account), and ACP-author the public type index.

`callsec.js` (the DTLS-fingerprint call auth) was reviewed and is fail-closed: a
MitM that rewrites the fingerprint is refused, and verification errors reject
rather than allow.

## Round 2 audit (fresh eyes: crypto, rendering, room correctness, regressions)

Fixed:
- **XSS/HTML-injection in the message renderer.** `rendering.js` interpolated a
  file attachment's `data_b64` into five `innerHTML` `src=`/`href=` attributes, a
  sender's `from_webid` into a `data-webid` attribute, and a peer's presence
  `status` into a `title` attribute, all unescaped — attacker-controlled in web
  mode. Now routed through the same `b64attr`/`escHtml` helpers the file already
  uses elsewhere. (Today's CSP `script-src 'self'` blocked inline handlers, so
  this was content/DOM injection, not arbitrary JS — but it defeated the escaping
  contract and would become stored XSS under any CSP relaxation.)
- **Received-DM persist gate (regression from the previous round).** The receive
  path had been gating BOTH the live render and the drop deletion on the
  best-effort history write; since a successful decrypt already advanced the
  ratchet, a cache-write failure (quota, private mode) permanently lost the
  message and wedged the drop. Now it renders and deletes unconditionally after
  decrypt and persists best-effort, non-gating.
- **Joined-room edit/delete/reaction/seq wrote to the wrong pod.** These secondary
  ops used the self-pod wrappers, so on a room hosted by another user they patched
  the editor's OWN pod (a no-op / phantom container) and never reached the shared
  copy. They now mirror the send path: a `_remoteRooms` room is written through
  the container-addressed helpers (`podEditChatMessageAt`,
  `podSoftDeleteChatMessageAt`, `podSetChatSeqAt`, `podWriteReactionActionAt`).
- **`voice_hangup` before answer.** `_callPeerWebid` is now set at invite receipt,
  so declining or closing a ringing call routes the hangup in web mode too.
- **First-contact DM authorship signing (R107).** The web-mode DM envelope now
  carries an Ed25519 signature over its canonical bytes (`from_webid`, message id,
  ciphertext, ratchet/key material, timestamp), signed by the sender's device
  `did:key` (`dmsig.js`). Each device publishes its signer identity at its own pod
  (`proxion/identity/signer.json`), and on receipt the recipient verifies the
  signature and confirms the signer is the one published at the SENDER's pod (or a
  device certified by the published account, via `device-cert.js`) — an anchor an
  attacker forging a "from alice" message cannot write to. Per the chosen
  transition policy it is non-gating: a verified DM shows no marker, an unsigned or
  unverifiable one still delivers but is marked "unverified sender", so nothing
  breaks for older clients or the gateway path. Follow-ups: sign the multi-device
  fanout envelope (single-device sends are covered; fanned-out copies currently
  show unverified), publish a device roster so any of an account's devices verifies
  without carrying a cert, and later tighten to reject once signing is widespread.
- **Verified safety-number not pinned to the key.** After a user verifies a peer,
  a later wire `x25519_pub` overwrites the pinned key without clearing the verified
  flag, so the badge can lie. Fix: key the flag by a key fingerprint; on change,
  clear it and warn.
- **Ratchet-state read-modify-write race.** A concurrent send and pod-drain
  receive can wholesale-overwrite each other's saved ratchet state and roll a
  chain back (the "already consumed" / dropped-message class). Fix: serialize
  ratchet ops per peer.
- **Plaintext fallback on encrypt failure.** `main.js` sends cleartext to the
  untrusted pod if `ratchetEncrypt` throws — a silent confidentiality downgrade.
  Fail closed for E2E-capable peers.
- **Identity X25519 key is extractable in localStorage** (and the state key
  derives from it), so any XSS exfiltrates the identity and all ratchet state.
  Fix: non-extractable CryptoKey in IndexedDB. A key-storage refactor.
- **Participant `acl:Write` allows tampering.** The Long Chat grant gives every
  participant Write on the shared container, so a participant can overwrite/delete
  others' messages or the index. Inherent to pure-WAC shared containers (dropping
  Write breaks legitimate edit/delete); needs a server-side `foaf:maker` check or
  a per-participant sub-container layout.
- **Cross-UTC-day edit/delete.** Edit/delete address the day file from the echo
  (server-clock) timestamp, not the client timestamp the message was written
  under, so a message sent seconds either side of 00:00 UTC is patched in the
  wrong `YYYY/MM/DD` file. Fix: preserve the client write timestamp as the stable
  pod-partition key across the echo merge.

### Manual checklist (only the parts not yet in a smoke)
The signed-in and join smokes now cover sign-in, room create, post, reload, and
the full join handshake. Still worth a human pass on the live deploy for:
1. Copy your WebID from Settings, have a second pod DM you, confirm it arrives
   (the storage-root fix above should make this work; verify on the live deploy).
2. Check presence shows, and a 1:1 call at least starts signaling.

Verification note that shapes everything here: the browser smoke
(`smoke_web_nogw.mjs`) only covers the signed-**out** boot. The signed-**in**
path (PodSocket, `postAuthInit`, room/DM/presence/call engines) has unit tests
and live-pod integration tests, but has never run assembled in a real browser,
because that needs OIDC-UI automation we deemed too brittle. Several items below
live in that blind spot.

## P0: correctness and data loss

### 1. A failed DM still shows as sent
`podtransport.js` runs `if (dm) await dm.dropDm(cmd); _echoDm(cmd);`, and the echo
that clears the optimistic "pending" state fires even when `dropDm` returned
`false`. So if the drop fails (see item 2), the sender sees a delivered-looking
message that never left. Silent loss, and misleading.
- **Fix:** only echo on a successful drop; on failure leave it pending so
  `send-status.js` marks it "Not delivered / Retry" (that machinery already
  exists for the gateway path). Same for `send_dm_fanout` (echo only if any
  entry dropped).

### 2. DMing someone who has not opened Proxion Web yet is lost
A DM is a POST into the recipient's `proxion/dm-inbox/` container. That container
is created by `podEnsureDmInbox()` when **they** sign in. Until then a POST to a
missing container is a 404 in CSS, so the first person to message a contact who
has never opened the web app loses the message (compounded by item 1, it looks
sent). The two testers must both sign in once before either can DM the other.
- **Fix options:** (a) on send, if the drop 404s, surface "they need to open
  Proxion once" rather than a silent success; (b) longer term, fall back to the
  standard LDN inbox (`podSendChatInvite` path) to nudge them; (c) document the
  "both sign in first" constraint in the beta instructions as a stopgap.

### 3. The signed-in boot is unverified in a browser
Every real tester takes the path the smoke never runs: OIDC redirect back ->
`onPodLoggedIn` -> `webBoot` logged-in branch -> PodSocket + `postAuthInit` ->
`get_rooms`, `webDm.start`, `webPresence.start`, `webCalls.start`. A single throw
there breaks the whole session for every user, and we would not know.
- **Fix:** get one authenticated browser run green. The blocker is CSS's login
  UI (a JS SPA needing a browser account cookie). Worth another attempt: drive
  CSS's `.account/login/password/` form directly, or seed the browser's CSS
  session cookie before the OIDC hop. Until then, a manual sign-in pass on the
  live deploy is the minimum before wider beta.

## P1: the subpath deploy breaks the PWA

The app was built to be served at the origin root (the gateway serves it at
`localhost:8080/`). On Pages it is served from `/proxion-messenger/app/`, and
every origin-absolute path breaks. Confirmed against the live deploy:

- `sw-register.js` registers `"/sw.js"` -> 404 at the origin root, so **the
  service worker never registers**. No offline, no installable PWA, and the
  absolute `SHELL` paths never get a chance to fail because install never runs.
- `<link rel="manifest" href="/manifest.json">` -> 404. **No manifest**, so the
  app is not installable and gets no name/theme/icons from it.
- `<link ... href="/icons/...">` -> 404. Broken home-screen and apple-touch
  icons.
- `manifest.json` `"start_url": "/"` -> an installed PWA would launch the landing
  page, not the app.

The app itself loads because `main.js` and `sw-register.js` are referenced with
**relative** `src`, and locale/module fetches are relative too. So this degrades
the "installable, offline PWA" story, it does not break the website.

- **Fix:** make these paths relative so they work at both the gateway root and
  the Pages subpath. `sw-register.js` -> `register("sw.js")`; index.html manifest
  and icon `href`s -> relative; `manifest.json` `start_url`/`scope`/icon paths ->
  `./`-relative; `sw.js` `SHELL` -> relative entries, and the navigation fallback
  `caches.match("/index.html")` -> scope-relative. Relative works at the root
  too, so it is one change for both builds. Add a `smoke:web` tier that serves
  the build from a **subpath** (not root) to catch regressions.

## P1: functional gaps in web mode

These are gateway features with no gateway-free equivalent yet. Testers will
notice their absence.

- **Cannot join someone else's room.** Rooms you create work; `join_room` is a
  gateway command the PodSocket ignores. Cross-pod join needs a handshake: the
  owner grants the joiner ACL on the room container (the `podSetContainerAcl`
  path exists) and shares the room descriptor, and the joiner reads and registers
  it. This is the biggest missing piece for two testers to share a room, and it
  is real work (a join-request drop box plus an owner-side grant), not a polish.
- **No contact discovery.** Adding a DM contact requires pasting their exact
  WebID. There is no search or "find me on Solid". At minimum, make the user's
  own WebID one-tap copyable so they can share it.
- **Calls need a TURN relay on restrictive networks.** Web calls use public STUN
  by default, which covers many home networks; symmetric/corporate NAT needs
  TURN, which is a relay server, not app code. The app already accepts a
  user-supplied relay. Ship a short doc on pointing it at one, and consider a
  shared TURN-only service for the beta.
- **No typing indicators or read receipts in web mode.** Both are gateway relay
  features. Lower priority; could ride the same pod-notification substrate later.
- **No device linking in web mode.** Pairing a second device uses the gateway.
  Multi-device DM fanout works if devices exist, but the web build has no way to
  add one.

## P2: smaller rough edges

- `postAuthInit` fires `fetch('/connectivity')` and `fetch('/health')` when
  Settings is opened; on Pages these 404 and the `Promise.all(...).then` has no
  `.catch`, so an unhandled rejection is logged and the federation panel stays
  blank. Guard it in web mode.
- The Settings pod-disconnect, import, and message-edit-history actions call
  gateway HTTP endpoints (`/api/pod-disconnect`, `/import`, `/message-edits`)
  that 404 on Pages. Hide or reroute them in web mode.
- DM and presence updates poll the pod on a timer (about 5s for DMs, 30s
  heartbeat for presence), so delivery and "online" can lag a few seconds. Fine
  for beta; note it so testers do not read lag as a failure.
- The web sign-in screen is translated, but the rest of the first-run help text
  still assumes the desktop wizard. A short web-specific "what to do next" would
  help.

## Suggested order

1. **P0 item 1** (echo only on successful drop) and **P0 item 2** surfacing:
   small, high-value, stops silent message loss. Add a live test asserting a
   drop to a missing inbox is reported, not swallowed.
2. **P1 subpath fix**: makes the deployed PWA installable and cacheable, plus a
   subpath smoke tier so it cannot regress.
3. **P0 item 3**: one authenticated browser run, or a documented manual pass,
   before widening the beta.
4. **P1 room join**: the largest feature gap; scope it as its own round.
5. **P1/P2 polish**: TURN doc, own-WebID copy, settings endpoint guards, the
   web help text.

Items 1 through 3 are what most protect a beta tester from a confusing or lossy
first session; 4 is the feature they will most want next.
