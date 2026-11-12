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

Inbox checks share one reader per DM engine. A notification during a read queues
a fresh scan, and startup subscribes before listing the inbox. Consumed drops
are remembered by canonical envelope SHA-256, scoped to the account and receiving
device. A failed DELETE retries acknowledgement without decrypting the same
ciphertext again, including after reload or an exact replay at another inbox URL.
IndexedDB retains at most 4,096 content-free receipts per account/device, evicting
acknowledged entries first. Pending receipts are protected; if the cap is full,
new ciphertext waits until acknowledgement or confirmed external deletion frees
capacity. The in-memory cache keeps its
1,024-entry bound. Local DM history holds plaintext separately; receipt storage
contains no message text or media keys. Storage failures fall back to live-only
behavior. Ratchet advancement and history/receipt writes are not one transaction,
so an abrupt crash before those writes commit can still lose a received message.

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
  The desktop reports cross-mode readiness only after publishing its signing
  identity, encryption key, and their public-read access rules. A rejected write
  leaves browser-contact routing unavailable and shows a publication error in
  Settings; reconnect retries publication. Duplicate bridge replies share one
  startup, and disconnect/account changes stop old inbox watches and discard late
  call events. Inbox reads, deletes, and subscriptions recheck the pod's signer;
  due inbox watches are revalidated as a batch. A takeover made through another
  browser or gateway therefore revokes the old desktop's inbox access, stops its
  inbox watches, and clears readiness. A failed signer refresh also refuses access
  rather than trusting an old cached signer. Reopening/reconnecting intentionally
  republishes this device's identity to reclaim receiving. These checks add signer
  reads to inbox operations; they do not implement an atomic lease across pods.
  This does not add browser device pairing or multi-device fanout.
- **Files and voice messages:** encrypted pod-backed attachments up to 2 MiB and
  recorded voice notes work through the browser and desktop chat controls. Source
  drafts survive upload/delivery failures and reload, with explicit Retry/Cancel.
  New drafts whose delivery was never attempted also offer confirmed cleanup of
  their unused pod chunks. Interrupted cleanup can retry after reload; possibly
  delivered and legacy uploads are retained. Cleanup is owner-only and uses
  exact ciphertext readback plus conditional deletion.
  Confirmed sends and verified downloads restore from local history. Browser and
  cross-mode DM smokes exercise exact file bytes and real recorded playback in
  both directions. See [the data model](POD_DATA_MODEL.md#encrypted-dm-assets-and-durable-media-delivery).
- **Not yet:** group calls, edits, reactions, media forwarding,
  disappearing and scheduled messages in these threads. One desktop device per
  WebID receives them: the one that signed in last.

In the browser, use **Send DM** from a room member's profile or add their Solid
WebID as a contact. Both entry points use the same pod-backed DM conversation.
**Add friend** in the message menu, member menu, and profile pre-fills a mutual
friend request. Recipients can accept or decline; confirmed people appear in
Friends. DM contacts remain separate from friendship. Self-adds and blocked
people cannot be added through these actions. Saved contacts, friendship state,
and local message history survive reload.

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
  signed `pins.json` index. Attachments additionally permit scoped chunk POSTs
  to that registered room container and GET/HEAD on its asset resources.
  The host is pinned after an SSRF check,
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

## Desktop pod-request scheduling

The desktop gateway processes socket commands serially. Its browser-side pod
bridge therefore sends one pod fetch at a time in FIFO order. The execution
timeout starts when the request is sent, so background history and inbox reads
cannot consume a later request's execution budget while it is still queued.
There are at most 256 pending requests, with a 60-second queue deadline and a
20-second execution deadline (the gateway's own-pod deadline is 15 seconds).
Overload and expired unsent work fail explicitly. Requests are not replayed on
failure; callers still need confirmed writes and their normal retry handling.

Disconnects, pod-account changes, and loss of pod readiness cancel active and
queued requests. Unsent writes are discarded, and late acknowledgements cannot
complete requests from a new connection. `web/gwpod.test.js` covers serial-server
latency, FIFO ordering, overload, queue expiry, disconnect/account changes, and
late responses. Room, DM, and call browser smokes exercise the shared transport.

## Gateway file and voice-note recovery

Gateway conversations retain their file-transfer and voice-note paths. Media
preparation captures the original conversation, socket, and account. Changing
any of them before dispatch cancels the pending send; an old file read or stopped
recording cannot target the newly opened conversation. An active recording also
releases its microphone after its context changes. Recorder startup and read
failures are surfaced instead of sending empty audio.

Gateway room and DM history now include saved voice-note audio, type, and
duration, so notes remain playable after reload. Existing participant checks
still protect those history responses. `web/smoke_media_context.mjs` exercises
real file selection, delayed reads, microphone recording, conversation switching,
and restored downloadable file bytes and playable audio. Backend voice-history
tests also verify that nonparticipants cannot retrieve the restored notes.

Browser/desktop pod rooms now use a separate shared attachment protocol (below);
the legacy own-pod copy helpers are not used to deliver those transfers.

## Shared room file and voice transfers

Both builds now support shared pod-room files up to 2 MiB and WebM voice notes.
Members upload into the owner's room container through immutable JSON chunks;
the signed room message binds the file metadata and full-file SHA-256 digest.
Load attachment performs an authenticated, bounded download and verifies bytes
before preview, playback, or download. Failed uploads retain drafts for retry,
including after reload. Participant removal and room closure revoke future reads
and uploads; the owner retains stored history and files. Pod-backed DMs use the
separate encrypted asset protocol described above.

The format, access rules, and draft behavior are specified in
[POD_DATA_MODEL.md](POD_DATA_MODEL.md#shared-pod-room-attachments-and-voice-notes).
`smoke_room_attachments.mjs` tests 2 MiB/64-chunk transfers, exact bytes, member
PUT denial, revoked reads/uploads and retained owner access. Browser room smokes
exercise failed uploads, reload/retry and playback. Cross-mode room smokes exercise
both host types, file transfer in both directions, recording from both builds,
and attachment access after deletion.

## Reviewing retained room files

Both builds expose Settings → Pod & sync → Stored room files for the connected
account's own pod. It lists physical room storage, including retained folders
without an active room descriptor, and navigates shared chunks and legacy file
or voice-copy folders. Directory listings use bounded JSON-LD GETs; files use
HEAD metadata only, with 50 entries per page. This requires no new resource
format, migration, or pod writes. Desktop access uses the owner-only pod bridge;
foreign directory and legacy-content access remain refused.

References are checked only against loaded, verified room attachment messages.
**Reference not verified** does not establish that a file is unused, and this
review has no deletion action. Missing metadata and incomplete listings remain
visible as uncertainty. `npm run smoke:asset-inventory` checks actual browser and
desktop controls with a throwaway Solid pod, including retained storage, folder
navigation, pagination, close/reset, and metadata-only reads.

## Private DM media forwarding

Browser and desktop clients can forward verified pod-DM file and voice
attachments to an existing WebID DM through the message Forward menu. The client
verifies the source download, re-encrypts with a fresh ID/key/IV, and uploads to
the forwarder's own asset storage. The destination receives a normal encrypted
attachment from the forwarder. No source key, source URL or original-author
claim is carried into that copy, and no plaintext gateway forwarding command is
used. There is no wire-format or bridge-policy change.

The destination opens with its ordinary durable media draft. Failed uploads or
delivery remain retryable across reload without downloading the original again.
Filenames, spoilers and voice duration are preserved. Source failure keeps the
picker available for retry; navigation/cancellation/account changes invalidate
pending work. Room targets and legacy gateway DMs are excluded from private
media forwarding. Existing text forwarding retains its previous behavior.
The browser DM and both cross-mode DM journeys verify forwarding with new keys,
exact downloaded bytes, durable retries, and playable forwarded voice notes.

## Automated ACP access checks

`npm run smoke:acp` starts isolated CSS 7 servers using their ACP file-storage
configuration, checks the advertised model, and runs required permission and
browser/desktop journeys. Temporary pod data is removed when the server exits.
`PROXION_CSS_ACP=1` also selects ACP for individual pod smokes; the default remains
WAC. CI runs the WAC room group and the ACP group, treating missing prerequisites
as a failure rather than silently skipping either target.

Coverage includes all four browser inbox types accepting public drops while
protecting listings and dropped resources; desktop inbox availability before the
UI opens; full-size shared and private attachments; encrypted forwarding; member
removal; retained owner access; and approval retry after reload. The standalone
permission smoke also invokes the Python DPoP client against the real server,
checking JSON-LD and Turtle room policies with two members and inherited child
access, then revocation, empty membership and read-only access.

The Python ACP writer now resolves internal stash URIs to HTTP resource identities,
uses the discovered same-origin ACR location, and writes the complete membership
snapshot once. Previously each member overwrote the same ACR, and empty membership
wrote nothing. Both encodings now apply grants to child resources. Model detection
recognizes CSS's `.acr` target advertised with `rel="acl"`, as well as explicit ACP
relations. Failed metadata probes stop grant writes and remain retryable. These
checks establish CSS ACP compatibility; they do not certify an Inrupt ESS deployment.

Presence publication also uses the discovered model. It previously wrote WAC
authorization into an ACP resource, leaving contacts unable to read heartbeats.
The ACP grant now provides public Read while preserving owner updates; policy
publication failures remain retryable. Live checks cover public reads, denied
peer writes, and presence displayed by both browser and desktop clients.

## Consumed DM drop reconciliation

Both builds now reconcile pending receive receipts when another client or a pod
tool removes the consumed inbox resource. Receipts can retain its validated URL;
the engine probes bounded, rotating pages through the existing owner-scoped HEAD
route. Confirmed 404/410 responses release pending capacity without decrypting
spent ciphertext or redelivering history. Other responses preserve pending state.
No new pod resource format, gateway permission or remote write is introduced.

Missing-resource acknowledgements conditionally match the currently stored URL,
and account/device/engine changes discard stale results. Exact envelope digests
remain available for replay suppression until normal bounded eviction. Legacy
receipts without a locator remain conservative until their envelope is observed
again. This does not make ratchet advancement and local history/receipt writes
transactional. Browser and cross-mode DM smokes retain a consumed drop, delete it
using an independent authenticated owner client, reload the receiver, verify
metadata-only reconciliation and retained history, then receive another DM.

## Gateway acceptance while a requester is offline

Gateway friend acceptance is stored as `accepting` before contacting the requester's
`/invite/accept` endpoint. The accepting gateway creates its contact only after a
successful HTTP confirmation. Its normal retry worker resumes the saved acceptance
across restarts; the request card survives reload and offers Retry. Ignore is disabled
while that acceptance is pending. Transient failures retry with exponential backoff
from 30 seconds to one hour. A signed withdrawal or HTTP 410 clears the pending
acceptance without adding a local contact; other terminal client errors also stop it.

The requester atomically records its relationship and the exact accepted certificate.
A replay of that same certificate returns success only while the recorded relationship
is active, without rewriting names, keys, or routing metadata. This handles a lost
confirmation response. A different certificate or a removed/expired relationship is
refused. Older requester gateways without acceptance receipts may refuse a retry after
losing the first response; the accepting side then reports failure rather than claiming
a confirmed contact. `web/smoke_federation.mjs` covers the offline/reload/restart path
before exchanging encrypted DMs and joining a federated room.
