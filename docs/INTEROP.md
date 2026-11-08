# Interoperability and server compatibility

This is the specific picture of what Proxion interoperates with, how, and
where the edges are. Every capability listed here is backed by a test in the repo,
named so you can run it yourself.

For a per-requirement audit against the full Solid specification suite (Compliant /
Partial / Missing / N-A by design, with citations), see
[SOLID_COMPLIANCE.md](SOLID_COMPLIANCE.md).

## What interoperates, and what does not

- **Rooms and shared conversations interoperate.** They are shared by design, so
  their history is plaintext, standard RDF that any authorized Solid app can read and
  write.
- **Direct messages do not, on purpose.** DMs are end-to-end encrypted. You cannot
  have bytes a third-party app can read and that no third party can read, so DMs stay
  Proxion-only and are deliberately not third-party readable. This is a design choice,
  not a gap. The encryption protocol is specified in
  [E2E_ENCRYPTION.md](E2E_ENCRYPTION.md) for any app that wants to implement it.

## Proxion desktop and Proxion in the browser

The desktop app (a gateway) and the browser build (no gateway, just your pod) can
direct-message and call each other. The browser build delivers a DM by dropping a signed,
ratchet-encrypted envelope into the recipient's `proxion/dm-inbox/`; the desktop
client runs that same code (`web/webdm.js`) as the WebID of the pod its gateway is
connected to, with the gateway making the pod requests on its behalf
(`_gateway_podbridge.py`, an owner-only and path-allowlisted fetch).

- **Works:** text DMs both ways, message requests for a first DM from a stranger,
  presence both ways, blocking. Test: `npm run smoke:cross-mode-dm`.
- **Calls:** 1:1 voice and video both ways. Call signaling (offer, answer, ICE,
  hangup) is dropped into the other side's `proxion/call-inbox/`; the desktop
  polls its own every second while a call rings or runs, and only its contacts
  can ring it. Media is peer to peer. Each side signs its DTLS fingerprint with
  its device key, and the other side reads Verified only when that key is the
  one named in the caller's `proxion/identity/signer.json`; any other key reads
  Unverified, or the call is refused once that contact has made a verified call.
  The desktop keeps its gateway's TURN relay; the browser build uses public STUN
  and the default or user-added relay. A call to a closed desktop app shows a
  missed-call notification within a minute. Test: `npm run smoke:cross-mode-call`.
- **Needs:** a pod connected to the desktop gateway, stored where its WebID points
  (`{root}profile/card#me` with storage at `{root}`). Share the WebID from
  Settings, Pod.
- **Not yet:** group calls, edits, reactions, files, voice messages,
  disappearing and scheduled messages in these threads. One desktop device per
  WebID receives them: the one that signed in last.

In the browser, use **Send DM** from a room member's profile or add their Solid
WebID as a contact. Both entry points use the same pod-backed DM conversation.
**Add friend** in the message menu, member menu, and profile pre-fills the
add-contact dialog. Saved people show **In contacts**; self-adds and blocked
people cannot be added through these actions.
The saved contact and local message history survive reload. Saved browser
contacts do not imply a mutually accepted friendship; that browser workflow is
tracked separately in the project plan.

Room owners see a persistent notice while join requests await a decision.
Requests retry after temporary inbox failures and listening resumes after
sign-in or when the app regains focus. Opening an invitation in a second tab
forwards it for review in the already signed-in browser tab when available;
the second tab keeps its messaging session inactive. Otherwise **Use here**
remains available. An existing member can reopen an invite to repair their
room registration without requiring a second owner decision.

Rooms shared across the two builds are pod rooms: a Long Chat in the owner's pod
(below). Gateway rooms stay desktop only. Members can edit and withdraw their own
messages, and owners can withdraw any message, using signed append-only change
records. Failed saves preserve the edit draft for retry; changes survive reload.
Signed reactions support adding, removing, and re-adding each user’s own emoji,
including after reload. These operations are currently available in pod rooms,
not cross-mode DMs. Other Solid apps need to implement the signed change-record
extension to display these reactions. Owners can also pin/unpin room messages;
members have a read-only pin panel. The owner-signed `pins.json` index keeps old
pins discoverable beyond the normal history window, and Jump reads their original
day files. Other Solid apps need this pin-index extension to discover pins.
Pod rooms also support creating polls and casting, changing, or clearing one
vote per member. Signed atomic choices survive reload in both builds. Editing
a voted poll asks the author to confirm resetting its votes; previous choices
remain bound to the old question. Other Solid apps can read the poll text and
need the signed vote extension to display counts.

- **Desktop joins a browser-build room** by pasting the invite link into Join a
  room. The join request, the owner's approval and the room messages travel
  through the pods, as between two browsers. After the approval the desktop
  registers the room's chat container with its gateway, and only then does the
  gateway send its pod credentials there: DPoP-signed `GET`, `HEAD` and `PATCH`
  on the chat files of that one container, plus read-only `GET`/`HEAD` on its
  signed `pins.json` index, the host pinned after an SSRF check,
  no redirects. A pod that signs in with a bearer token (JSS, NSS) never sends
  that token to another pod, so those desktops cannot join and are told why.
- **Desktop hosts a room browser-build users can join:** Create a room, "Create it
  in my pod". The invite link opens the browser build
  (`https://cafetechne.github.io/proxion-messenger/app/`), and the owner approves
  join requests on the desktop.
- Messages are signed by the device key the WebID's `signer.json` names, with
  `foaf:maker` set to the WebID, and each side verifies the other's. The owner
  removes a member by rewriting the chat ACL; the removed member's next append
  is refused (403) and their app shows the removal. Test:
  `npm run smoke:cross-mode-rooms`.

## What another Solid app sees

A room is written as a standard Solid [Long Chat](https://solid.github.io/chat/): a
channel resource plus date-partitioned message files, in typed RDF.

| Term | Namespace | Carries |
|---|---|---|
| `meeting:message` and `wf:message` | `pim/meeting#`, `01/wf/flow#` | channel to message links (both are emitted; SolidOS reads `wf:message`, the spec and POD-CHAT read `meeting:message`) |
| `sioc:content` | `rdfs.org/sioc/ns#` | the message text |
| `foaf:maker` | `xmlns.com/foaf/0.1/` | the author, as a WebID IRI |
| `dct:created` | `purl.org/dc/terms/` | the send time, `xsd:dateTime` |
| `schema:dateDeleted` | `schema.org/` | a soft-delete tombstone |
| `px:seq` | `proxion.dev/vocab/v1#` | a per-message order hint (ours; other apps ignore it) |

Proxion also writes its own `px:` terms in the same graph for the things the shared
vocabulary has no term for (reply context, reactions, content-type). Other apps
ignore what they do not understand; nothing is lost.

## Capabilities, and the test that proves each

- **Renders in SolidOS.** A Proxion room opens as a chat in the real SolidOS
  databrowser, messages and authors intact. The legacy gateway mirroring path
  also displays edits and deletes. Pod-native room changes use signed append-only
  `px:MessageChange` records; other readers must support this extension to show
  edits or withdrawals, otherwise they retain the original message.
  Proven by driving the actual mashlib databrowser: `web/solidos-render.test.js`,
  `web/solidos-render-edits.test.js`. This method caught a real spec gap (the
  databrowser reads `wf:message`, not the spec's `meeting:message`), which is why
  both link predicates are emitted.
- **Discoverable via the type index.** Each room registers as a
  `solid:TypeRegistration` for `meeting:LongChat` in the pod's public type index, so
  another app finds it without a URL. `web/podcanonical-typeindex.test.js`.
- **Discover another WebID's chats.** Reading a WebID's public type index lists the
  chats it hosts, joinable through the access-checked join.
  `web/discover.test.js`, `web/podcanonical-discover-live.test.js`.
- **Invitations via the Solid inbox.** Hosting with a participant drops an
  ActivityStreams `Invite` in their `ldp:inbox` (Linked Data Notifications), which any
  Solid app can produce or consume. `web/ldn.test.js`,
  `web/podcanonical-ldn-live.test.js`.
- **Real-time over Solid Notifications.** New posts arrive over a
  `WebSocketChannel2023` subscription, with polling as a fallback. Inbox invitations are
  watched the same way, and reach a closed app via the `WebhookChannel2023` -> gateway
  -> Web Push bridge or an outbound poll. `web/podcanonical-notify.test.js`,
  `web/notify.test.js`, `web/podcanonical-inboxwatch-live.test.js`. See
  [NOTIFICATIONS.md](NOTIFICATIONS.md).
- **Cross-identity shared chat.** A second identity, granted write access, posts to a
  chat hosted in someone else's pod. `web/longchat-shared.test.js`,
  `web/podcanonical-b4.test.js`.
- **Rooms outlive the gateway.** A room's structure lives in the host's pod as an
  Ed25519-signed descriptor a fresh gateway rebuilds it from.
  `proxion-messenger-core/tests/e2e/test_rehost.py`.

## Server compatibility

| Server | Read/write Long Chat | Notifications | ACL model | Notes |
|---|---|---|---|---|
| Community Solid Server 7.x | Yes | Yes (WebSocketChannel2023, v0.3) | WAC or ACP | Primary target, fully exercised in tests |
| solidcommunity.net (CSS) | Yes | Depends on deployment config | WAC | Same engine as above |
| Inrupt PodSpaces (ESS) | Read/write expected (standard RDF) | Not via our client path | ACP | See caveats below |

Caveats worth stating:

- **Notifications: v0.3 protocol, spoken directly.** We do not use
  `@inrupt/solid-client-notifications`. Verified live, that library (v3) looks for an
  old-style negotiation gateway and reports CSS 7 as unsupported, while CSS advertises
  a subscription service in its storage description (the v0.3 model). Proxion speaks
  v0.3 directly, and falls back to polling on any server that offers no service.
- **Access control: WAC or ACP.** Proxion reads the server's access-control model from
  the resource's `Link` header and writes a WAC ACL or an ACP ACR to match
  (`web/acl.js`, `podSetContainerAcl`). ACP authoring is verified live against CSS in ACP
  mode ([SOLID_COMPLIANCE.md](SOLID_COMPLIANCE.md)).
- **Identity: a pod-less user shows as an id.** A `did:key`-only identity has no
  dereferenceable WebID card, so other apps display the identifier rather than a name.
  That is the expected limit of interop for users who have not connected a pod.

## Order across devices and members

Message order uses a per-message `px:seq` (the gateway's single-clock time) when
present, falling back to timestamp otherwise, so a user's devices, and members of a
single-pod chat, agree on order despite client clock skew.
`web/longchat-order.test.js`, `web/podcanonical-d4.test.js`.

## Browser mutual friendships

Browser accounts can exchange, accept, decline, withdraw, and remove mutual
friend requests through actor-owned pod records. This protocol is separate from
DM contacts and gateway contact certificates; desktop clients do not yet consume
these friendship records. Existing Add contact and browser/desktop DM flows
retain their behavior. The resource format and access rules are documented in
[POD_DATA_MODEL.md](POD_DATA_MODEL.md#browser-friendship-requests).

`web/smoke_web_friends.mjs` exercises two real browser accounts with a local Solid
server: failed writes, delayed delivery, reload recovery, acceptance, removal,
decline, withdrawal, DM delivery, and recipient/anonymous access boundaries.
`web/friendio.test.js` covers conflict merging, key changes, stale request IDs,
forged notifications, failed writes, account switches, and competing decisions.
