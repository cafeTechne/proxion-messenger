# Proxion Pod Data Model

This document is the authoritative description of how Proxion stores your data on
a [Solid](https://solidproject.org) pod. It exists so that **any Solid app, not
just Proxion, can read and reuse your data**. That is the whole point of building
on Solid: your messages are open, typed resources in a datastore you control, not
rows in someone else's database.

## The philosophy, made concrete

Proxion draws a deliberate line between two things that are easy to conflate:

- **Encryption in transit (end-to-end).** Direct messages are end-to-end
  encrypted on the wire between you and your contact, so no relay or gateway that
  forwards them can read them. This is a property of *delivery*. The protocol
  and its scope are in [E2E_ENCRYPTION.md](E2E_ENCRYPTION.md).
- **Storage at rest (open).** What lands on your own pod is written as plain,
  typed JSON-LD, using a documented vocabulary. It is **not** an encrypted blob.
  Any app you authorize can read it. This is a property of *storage*.

Because these are separate, Proxion can be private against the infrastructure in
the middle while still keeping your pod an open, interoperable store. This
document describes the storage side.

## Namespace

All Proxion-specific terms use one namespace:

```
https://proxion.dev/vocab/v1#
```

conventionally bound to the prefix `px:`. Every JSON-LD document below carries
`"@context": { "px": "https://proxion.dev/vocab/v1#" }`. Until a machine-readable
ontology is served at that URL, **this document is the definition** of the terms.
Access control uses the standard Web Access Control vocabulary,
`http://www.w3.org/ns/auth/acl#` (prefix `acl:`).

## Container layout

Everything lives under a single container, `{pod}/proxion/`:

```
{pod}/proxion/
├── profile/
│   ├── display_name.jsonld        px:Profile
│   └── avatar.png                 (binary image)
├── rooms/
│   ├── index.jsonld               px:Index (room ids)
│   └── {roomId}/
│       ├── members.jsonld         px:MemberList
│       ├── pins.json              owner-signed durable pin references
│       ├── messages/
│       │   └── {messageId}.jsonld px:Message
│       ├── reactions/
│       │   └── {messageId}.jsonld px:ReactionSet
│       └── files/
│           ├── {messageId}.webm   (binary voice note)
│           └── {messageId}/{name} (binary attachment)
├── dm/                            (only when DM pod archive is enabled; opt-in)
│   ├── index.jsonld               px:Index (thread ids)
│   └── {threadId}/
│       └── messages/
│           ├── index.jsonld       px:Index (message ids)
│           └── {messageId}.jsonld px:Message
├── saved/                         (only when bookmarks+settings sync is enabled; opt-in)
│   ├── index.jsonld               px:Index (saved message ids)
│   └── {messageId}.jsonld         px:SavedMessage
├── settings.jsonld                px:Settings (only when sync enabled; opt-in)
├── mutes.jsonld                   px:MuteList (only when sync enabled; opt-in)
├── blocks.jsonld                  px:BlockList (only when sync enabled; opt-in)
├── gifs/                          (only when sync enabled; opt-in)
│   ├── index.jsonld               px:Index (gif ids)
│   ├── {id}                       (binary image, real content type)
│   └── {id}.jsonld                px:GifFavorite (metadata + image ref)
├── contacts/
│   ├── index.jsonld               px:Index (cert ids)
│   └── {certId}.jsonld            px:Contact
├── invites/
│   ├── index.jsonld               px:Index (invitation ids)
│   └── {id}.jsonld                px:PendingInvite
├── readstate/
│   └── {threadId}.jsonld          px:ReadState
├── scheduled/
│   └── {id}.jsonld                px:ScheduledMessage
└── webhooks/
    └── {id}.jsonld                px:Webhook
```

Identifiers (`roomId`, `threadId`, `messageId`, `certId`) match
`^[\w-]{1,128}$`. Timestamps are ISO 8601 strings.

## Interoperability with the Solid chat ecosystem

Other Solid chat apps (the [SolidOS](https://solidos.org/) databrowser's Long
Chat, POD-CHAT) share one vocabulary, so they can read each other's chats. Room
messages written by Proxion carry those same standard terms **in addition to**
the `px:` ones, in the same JSON-LD graph, so another Solid app can read a
Proxion room without understanding anything Proxion-specific:

| Standard term | Namespace | Carries |
|---|---|---|
| `sioc:content` | `http://rdfs.org/sioc/ns#` | the message text |
| `foaf:maker` | `http://xmlns.com/foaf/0.1/` | the author, as an IRI (WebID) |
| `dct:created` | `http://purl.org/dc/terms/` | the send time, as `xsd:dateTime` |

Nothing is lost by this: apps that do not understand `px:` ignore it, and Proxion
keeps using `px:` for the things the shared vocabulary has no term for
(reactions, forwarding, content-type nuance, cached reply snippets).

**Rooms interoperate; direct messages deliberately do not.** This split is the
direct consequence of end-to-end encryption. You cannot have bytes that a
third-party app can read *and* that no third party can read. Rooms are shared by
design, so their history is plaintext and open. DMs are end-to-end encrypted, so
they stay `px:`-only and are not third-party readable, on purpose.

A pod-less identity is a `did:key`, which is a valid IRI but is not
dereferenceable, so other apps will show the identifier rather than a name. That
is the expected limit of interop for users who have not connected a pod.

**Pod-native room edits, withdrawal, and reactions** use signed append-only change records.
The original message and its proof remain intact. An operation is a separate
`px:MessageChange` node at `chat.ttl#change-<UUID>` in the original message's UTC
day file. Its `px:changePayload` literal contains JSON with `id`, `target` (the
original message IRI), `actor` (WebID), `kind` (`edit`, `delete`, `react`, `unreact`, or `vote`), `created`
(canonical UTC ISO timestamp), `content`, `previous` (revision IRI or empty), and
`proof` (`did:key|base64-signature`). `px:` is `https://proxion.dev/vocab/v1#`.

The signature uses the same four-byte length-prefixed UTF-8 framing as message
proofs (big-endian byte lengths, with `0x7c` between framed fields), over
`domain`, `id`, `target`, `actor`, `kind`, `created`, `content`, and
`previous`, in that order; the domain is `proxion-room-change-v1`. Readers require
the actor's published key. Only the original author may edit; either the author
or room owner may withdraw. Deletion has empty content and wins permanently over
edits. Otherwise the greatest `(created, id, proof)` tuple using code-unit string
ordering selects the displayed revision. `previous` enables preflight stale-draft
checks; concurrent append-only operations still resolve by that deterministic rule.
The client re-reads the file to confirm the selected result before reporting success.

Reactions use `content` as the reaction key (up to 64 UTF-16 code units / 128
UTF-8 bytes, with no whitespace, controls, or object-prototype keys). The latest
signed operation for each `(target, actor, reaction key)` tuple determines whether
that actor's reaction is present. `previous` refers to that actor's previous
operation for the same key, separately from the text-edit revision. The timestamp
advances beyond the previous operation even when a local clock moves backwards.
Only the actor's published signer can add or remove their reaction. Other users,
including the owner, cannot remove that actor's reaction by signing as themselves.
Removing then re-adding creates new operation IRIs, avoiding the permanent
cancellation of the legacy fixed-ID LikeAction representation. Message withdrawal
hides all reactions. Reactions do not change the text or its edit marker.

Reaction writes are serialized per message and session, reread for confirmation, and
idempotent on retry if the desired state already exists. Browser and desktop
readers project the same sorted reactor sets. Room synchronization observes those
sets without generating incoming-message counts or text-edit events. This signed
extension is currently pod-room specific; cross-mode DMs remain gated.

**Pod-room polls** use the structured plain-text poll format and signed `vote`
operations in that same day file. Each operation selects one keycap option in
`content`, or clears the choice with empty content. It also carries
`poll_revision`: the lowercase SHA-256 hex digest of UTF-8 compact JSON
`[revision_id || "", content]` for the displayed message. Vote signatures use
domain `proxion-room-vote-v1` and append `poll_revision` after the ordinary change
fields. This prevents replaying a vote as a reaction or against another question.

For each actor and current poll revision, the greatest `(created, id, proof)`
tuple selects that actor's single choice. Generic keycap reactions do not count
as pod poll votes. A move or clear appends one operation, then rereads to confirm;
an uncertain response offers Retry with the same desired choice and revision.
Editing a poll with active votes requires confirmation and changes the revision,
so votes for its previous question cannot count toward the edited question.
In-flight votes against an old revision report that the poll changed. Other
Solid apps can read the question/options as text; counting votes requires this
extension. Gateway-native polls retain their existing reaction transport.

Participants retain Read + Append permissions. Changes cannot erase original
text from the pod log. Other Solid apps need to implement this extension to show
edits/withdrawals/reactions; a reader of only `sioc:content` still sees the original message.
Proxion refreshes the loaded seven-day window with current-day notifications and
an older-day backstop rotation, so historical changes can take six poll intervals.

**Durable pod-room pins** live at `proxion/rooms/{roomId}/pins.json`.
The owner may pin or unpin; participants can read the panel. The JSON snapshot
contains `v: 1`, `index` (its full URL), `owner` (WebID), `revision` (UUID),
`updated` (canonical UTC ISO timestamp), `pins`, and `proof`. Each pin is
`{target, created, id}`: an original message IRI, its pin timestamp, and a unique
pin revision. The snapshot stores references, not copies of message text.

The owner signs the same length-prefixed UTF-8 framing used above, over `domain`,
`index`, `owner`, `revision`, `updated`, and `pins`; the domain is
`proxion-room-pins-v1`. The last field is compact JSON with pins sorted by target
using code-unit ordering, and fields in `target`, `created`, `id` order. Readers
check the owner against the room record and verify the proof against that WebID's
published signer. Invalid snapshots fail explicitly rather than becoming an
empty replacement index.

Creation uses `If-None-Match: *`; updates require a strong `ETag` and `If-Match`.
Conflicts re-read and reapply the operation (up to four attempts), preserving
concurrent owner-device changes. Success requires a fresh signed read confirming
the desired result. Each panel Unpin action also carries the pin revision, so a
stale action cannot remove a message re-pinned on another device. Lost-response
retries acknowledge an already-saved desired state without duplicate writes.
The compact snapshot supports 50 pins and a 64 KiB body. Before enforcing the
limit, the writer reclaims slots of withdrawn messages only after successful day
reads. Failed reads never authorize replacing the index from a cache.

Pin targets must be safe message IDs in the same room's dated `chat.ttl` files;
foreign URLs, traversal, invalid calendar dates, and alternate resource paths
are rejected. Reading the panel groups pins by UTC day and reads each original
day once, independently of the ordinary seven-day history window. It shows the
latest authenticated edits and omits withdrawn messages. Jump re-reads the target
and loads older history before scrolling. Session/view guards prevent delayed
results from populating another account or conversation.

While open, the panel watches `pins.json`, refreshes pinned text on message-change
notifications, and uses a 30-second backstop. Closing, changing conversation, or
losing access releases its watch and timer. The desktop bridge permits only
`GET`/`HEAD` on a joined room's registered pin index; owner `PUT` requests stay
under its own pod and bind the body's owner/index to the authorized path.
Pin indexes inherit room membership access, including revocation on closure.
Other Solid readers need this extension to discover these pins; Long Chat
message data stays in its existing format.

**Legacy gateway room mirroring** uses the following representation, previously
verified against the real SolidOS databrowser. This is separate from pod-native
room operations above:

- An **edit** rewrites the message's `sioc:content` in place (a SPARQL-Update
  `DELETE`/`INSERT`/`WHERE` on the day file), so the latest text shows in any
  reader of `sioc:content`. This is used in preference to a `dct:isReplacedBy`
  replacement chain, whose rendering is reader-dependent; the `px:` layer keeps
  full edit history.
- A **delete** appends a `schema:dateDeleted` tombstone (`http://schema.org/`).
  The message node stays so the append-only day file remains valid, and other
  Solid apps (the SolidOS databrowser) hide a tombstoned message.

  Under `acl:Append`, though, any participant can append a tombstone onto anyone's
  message, so a bare tombstone would let a member censor another member's message,
  even a signed one (the signature does not cover `schema:dateDeleted`). Proxion
  therefore requires an **authenticated** deletion: the deleter also writes a
  `px:deleteProof` holding an Ed25519 signature over the message IRI and the
  deletion time, packed as `"<did:key>|<base64-signature>"`. On read, Proxion
  honours a tombstone (hides the message) only when this proof verifies **and** the
  signer is authorized: the message's `foaf:maker` (a self-delete) or the room
  owner (moderation). A tombstone with no valid proof, or one signed by anyone
  else, is **ignored** and the message stays visible.

  Interop consequence: a delete made by another Solid app carries no
  `px:deleteProof`, so Proxion cannot tell it apart from a forged tombstone and
  will **not** hide it. This is the safe posture (never hide content on an
  unauthenticated tombstone). Other apps still honour the `schema:dateDeleted`
  tombstone as before.

Not yet mapped: replies and threads (`sioc:has_reply`, `sioc:Thread`). Long Chat
models replies on the parent message while Proxion models them on the child, so
that mapping is being prototyped against a real SolidOS thread rather than
guessed. Those relations remain `px:`-only until verified.

**Message signatures.** Each room message Proxion writes also carries a
cryptographic signature, following the [Solid chat SHACL
shape](https://github.com/solid/shapes/blob/main/shapes/chat.ttl):

| Standard term | Namespace | Carries |
|---|---|---|
| `sec:proofValue` | `https://w3id.org/security#` | an Ed25519 signature over the message |

The signature covers the message's core fields: its IRI (`@id`), the
`dct:created` time, the `sioc:content` text, the `foaf:maker` author, and the
author's display name (`px:fromName`), framed as length-prefixed bytes so no
field can be shifted into another. The display name is included so a validly
-signed message cannot carry a name that spoofs another author; a `px:fromName`
changed or appended after signing fails verification. Because the shape allows one
literal, the value packs the signer's `did:key` and the base64 signature as
`"<did:key>|<base64-signature>"`, so a reader has both the key and the signature
from the single term. A verifier accepts a legacy signature over just the four
core fields (from before the name was signed) only when no name is present, so the
legacy shim can never bless an appended name.

On read, Proxion verifies the signature and then confirms the signer is one the
author published at their **own** pod (the same trust anchor a direct message
uses; see `proxion/identity/signer.json`), which is what lets a reader trust that
`foaf:maker` is really the author and not a value a third party wrote into a
shared container. A message shows as **verified** only when both checks pass.

A message with **no** signature, an invalid one, or a signer the author has not
published shows as **unverified**, but is still read and displayed, never
dropped. This is required for interoperability: SolidOS and POD-CHAT write
unsigned messages, and an edit that rewrites `sioc:content` in place changes the
bytes the original signature covered, so a legacy in-place edited message also reads as
unverified. Pod-native signed revisions preserve the original message proof and
authenticate the change separately.

### The Long Chat container layout

Carrying the right terms is not enough for another app to *open* a Proxion room;
the layout has to match too. So each room is additionally written in the standard
Long Chat shape, next to the `px:` archive:

```
proxion/rooms/{roomId}/index.ttl              <#this> a meeting:LongChat
proxion/rooms/{roomId}/YYYY/MM/DD/chat.ttl    that day's messages
```

A day file links each message to the channel and then describes it:

```turtle
<../../../index.ttl#this> meeting:message :m-abc123 .
<../../../index.ttl#this> wf:message :m-abc123 .

:m-abc123
    dct:created "2026-07-22T14:03:11.000Z"^^xsd:dateTime;
    sioc:content "Morning, everyone";
    foaf:maker <https://alice.pod.example/profile/card#me>;
    px:fromName "Alice";
    sec:proofValue "did:key:z6Mk...|Base64Signature==".
```

Details worth stating because they are easy to get wrong:

- **Both** linking predicates are emitted: `meeting:message` (the written spec
  and POD-CHAT) and `wf:message` (`http://www.w3.org/2005/01/wf/flow#`). The
  SolidOS databrowser enumerates a channel's messages with `wf:message`, so a
  chat carrying only `meeting:message` is invisible in the reference app. This
  was found by driving the real databrowser, not by reading the spec.
- The channel title uses Dublin Core **Elements** (`dc:`), while message
  timestamps use Dublin Core **Terms** (`dct:`). Different namespaces.
- Day partitioning uses the message's **UTC** date, as the spec requires.
- Messages are appended with a SPARQL-Update `PATCH`, not read-modify-write, so
  two devices writing the same day do not clobber each other.

**Reactions** are mirrored into the day file as a `schema:LikeAction` targeting
the message (`schema:agent` the reactor, `sioc:content` the emoji), and an
append-only un-react tombstones that action with `schema:dateDeleted`. Both are
authenticated the same way a delete is: the reactor writes a `px:reactProof` over
the action IRI, target IRI, agent and emoji, and only that agent may cancel,
writing a `px:cancelProof` over the action IRI and cancel time. Proxion trusts a
reaction's agent only when its proof verifies for the agent's published signer,
and honours a cancel only when it is signed by that same agent, so a reaction
cannot be forged as another person and only its own author can withdraw it. A
reaction written by another Solid app carries no proof and is not trusted by
Proxion (but is still visible to that app).

Reading goes the other way: Proxion requests these resources as JSON-LD via
content negotiation, so it can display a chat written by SolidOS or POD-CHAT
without shipping an RDF parser to the browser.

The `px:` per-message JSON-LD documents described below are still written and
remain the canonical Proxion copy; the Long Chat layout is the interoperable
view of the same conversation.

## Resource types

### px:Message

A single message in a room or DM thread. Path:
`proxion/rooms/{roomId}/messages/{messageId}.jsonld` or
`proxion/dm/{threadId}/messages/{messageId}.jsonld`. Room messages are archived
automatically when a pod is connected; **DM messages only when you turn on the
opt-in DM pod archive** (see "What is deliberately NOT on the pod" below).

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:Message",
  "@id": "https://alice.pod.example/proxion/rooms/general/messages/m-abc123.jsonld",
  "px:messageId": "m-abc123",
  "px:threadId": "general",
  "px:content": "Morning, everyone",
  "px:contentType": "text",
  "px:fromWebid": "https://alice.pod.example/profile/card#me",
  "px:fromName": "Alice",
  "px:timestamp": "2026-07-20T14:03:11.000Z",
  "px:replyToId": null,
  "px:replyToSnippet": null,
  "px:forwarded": false,
  "px:forwardedFromName": null
}
```

| Term | Type | Meaning |
|------|------|---------|
| `px:messageId` | string | Stable id, unique within the thread |
| `px:threadId` | string | Room id or DM thread id this belongs to |
| `px:content` | string | The message body, in plain text |
| `px:contentType` | string | `text`, `audio`, etc. |
| `px:fromWebid` | string | Sender's WebID or `did:key` |
| `px:fromName` | string | Sender's display name at send time |
| `px:timestamp` | string | ISO 8601 send time |
| `px:replyToId` | string / null | Id of the message this replies to |
| `px:replyToSnippet` | string / null | Cached preview of the replied-to message |
| `px:forwarded` | boolean | Whether this message was forwarded |
| `px:forwardedFromName` | string / null | Original author name, if forwarded |

### px:Profile

`proxion/profile/display_name.jsonld`. The avatar, if set, is a plain PNG at
`proxion/profile/avatar.png`.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:Profile",
  "px:displayName": "Alice",
  "px:updatedAt": "2026-07-20T14:00:00.000Z"
}
```

### px:MemberList

`proxion/rooms/{roomId}/members.jsonld`. `px:members` is an array of member
descriptors (WebID or `did:key` plus display name).

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:MemberList",
  "px:roomId": "general",
  "px:members": [
    { "webid": "https://alice.pod.example/profile/card#me", "name": "Alice" }
  ],
  "px:updatedAt": "2026-07-20T14:00:00.000Z"
}
```

### px:ReactionSet

`proxion/rooms/{roomId}/reactions/{messageId}.jsonld`. `px:reactions` maps an
emoji (or `:custom_name:`) to the list of reactors.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:ReactionSet",
  "px:messageId": "m-abc123",
  "px:reactions": { "👍": ["https://alice.pod.example/profile/card#me"] },
  "px:updatedAt": "2026-07-20T14:05:00.000Z"
}
```

### px:ReadState

`proxion/readstate/{threadId}.jsonld`. The last message you have read in a thread,
for cross-device read sync.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:ReadState",
  "px:threadId": "general",
  "px:lastReadMessageId": "m-abc123",
  "px:updatedAt": "2026-07-20T14:06:00.000Z"
}
```

### px:Contact

`proxion/contacts/{certId}.jsonld`. Wraps the relationship certificate that
authorizes a contact. `px:certificate` is the certificate object.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:Contact",
  "@id": "https://alice.pod.example/proxion/contacts/cert-xyz.jsonld",
  "px:certId": "cert-xyz",
  "px:certificate": { "...": "certificate fields" },
  "px:updatedAt": "2026-07-20T14:00:00.000Z"
}
```

### px:SavedMessage

`proxion/saved/{messageId}.jsonld`, enumerated by `proxion/saved/index.jsonld`.
A private bookmark (a snapshot of a saved message, not a live copy). Written
only when the opt-in bookmarks + settings sync is enabled; owner-only, because a
bookmark can quote a DM.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:SavedMessage",
  "px:messageId": "m-abc123",
  "px:threadId": "general",
  "px:threadType": "local_room",
  "px:threadLabel": "general",
  "px:fromName": "Alice",
  "px:content": "worth remembering",
  "px:hasFile": false,
  "px:fileKind": "",
  "px:timestamp": "2026-07-20T14:03:11.000Z",
  "px:savedAt": 1721480400000,
  "px:updatedAt": "2026-07-20T14:10:00.000Z"
}
```

### px:Settings

`proxion/settings.jsonld`. A single document holding your synced, account-level
app preferences under `px:prefs` (a plain string map). Written only when sync is
enabled. Device-specific preferences (local DM-history cache, the sync toggles
themselves, gateway URL) are deliberately excluded.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:Settings",
  "px:prefs": {
    "proxion_receipts_enabled": "1",
    "proxion_link_previews_enabled": "0",
    "proxion_locale": "de"
  },
  "px:updatedAt": "2026-07-20T14:10:00.000Z"
}
```

### px:MuteList

`proxion/mutes.jsonld`. The set of conversations you have muted, as a plain
list of thread ids, so muting follows you across devices. Written only when sync
is enabled; owner-only.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:MuteList",
  "px:threads": ["general", "dm-thread1"],
  "px:updatedAt": "2026-07-20T14:10:00.000Z"
}
```

### px:BlockList

`proxion/blocks.jsonld`. The WebIDs you have blocked, as a plain list, so a
block follows you across devices. Written only when sync is enabled; owner-only.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:BlockList",
  "px:webids": ["did:key:zEvil"],
  "px:updatedAt": "2026-07-20T14:10:00.000Z"
}
```

### px:GifFavorite

`proxion/gifs/{id}.jsonld`, enumerated by `proxion/gifs/index.jsonld`. A saved
GIF-tray favorite. The image itself is stored as a real binary resource at
`proxion/gifs/{id}` (with its true content type, so other apps see an image, not
base64 in JSON); this doc is metadata referencing it. `{id}` is the SHA-256 of
the image (content-addressed, deduplicated). Written only when sync is enabled;
owner-only.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:GifFavorite",
  "px:gifId": "9f86d0818...",
  "px:filename": "reaction.gif",
  "px:mime": "image/gif",
  "px:image": "https://alice.pod.example/proxion/gifs/9f86d0818...",
  "px:addedAt": 1721480400000,
  "px:updatedAt": "2026-07-20T14:10:00.000Z"
}
```

### px:ScheduledMessage

`proxion/scheduled/{id}.jsonld`. A message queued for future delivery. Only a
preview is stored, never the full pending body.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:ScheduledMessage",
  "px:id": "sched-1",
  "px:threadId": "general",
  "px:sendAt": "2026-07-21T09:00:00.000Z",
  "px:contentPreview": "Reminder: standup",
  "px:createdAt": "2026-07-20T14:00:00.000Z"
}
```

### px:Webhook

`proxion/webhooks/{id}.jsonld`. An integration endpoint. The secret is stored only
as a SHA-256 hash (`px:tokenHash`), never in the clear.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:Webhook",
  "px:id": "wh-1",
  "px:direction": "incoming",
  "px:botName": "CI Bot",
  "px:url": null,
  "px:tokenHash": "9f86d0818...",
  "px:createdAt": "2026-07-20T14:00:00.000Z"
}
```

### px:PendingInvite

`proxion/invites/{id}.jsonld`. An invitation you have received but not yet
accepted. `px:invite` is the invite object.

### px:Index

Several containers keep a companion `index.jsonld` listing the ids of the
resources beside it, so a reader can enumerate without a container `LIST`.

```json
{
  "@context": { "px": "https://proxion.dev/vocab/v1#" },
  "@type": "px:Index",
  "px:ids": ["general", "team-standup"],
  "px:updatedAt": "2026-07-20T14:00:00.000Z"
}
```

Indexes exist for rooms (`proxion/rooms/index.jsonld`), DM threads
(`proxion/dm/index.jsonld`), contacts, and invites. Room message lists are
enumerable both by the container itself and by a companion index.

## Binary resources

The optional own-pod copies of gateway attachments and voice notes use ordinary
files with their real content types. These legacy copy helpers are separate from
delivery through the gateway:

- Voice notes: `proxion/rooms/{roomId}/files/{messageId}.webm` (`audio/webm`)
- File attachments: `proxion/rooms/{roomId}/files/{messageId}/{filename}`
- Avatar: `proxion/profile/avatar.png`

Attachment copy helpers return a URI only after a successful HTTP write in the
same authenticated account and pod root. Rejected writes, redirects, and account
changes return null. This acknowledgement does not verify readback or grant room
participants access. The helpers neither upload into another owner's room nor
provide browser/desktop pod-room transfers. Shared pod rooms use the separate
chunked protocol described below.

## Access control

Proxion writes standard [Web Access Control](https://solidproject.org/TR/wac)
ACLs, so sharing is enforced by the pod server, not by Proxion:

- The pod owner gets `acl:Read, acl:Write, acl:Control` on `proxion/`.
- For a shared room container, each member WebID is granted `acl:Read`.

An example room ACL:

```turtle
@prefix acl: <http://www.w3.org/ns/auth/acl#>.

<#owner>
    a acl:Authorization;
    acl:agent <https://alice.pod.example/profile/card#me>;
    acl:accessTo <.../rooms/general/>;
    acl:default <.../rooms/general/>;
    acl:mode acl:Read, acl:Write, acl:Control.

<#members>
    a acl:Authorization;
    acl:agent <https://bob.pod.example/profile/card#me>;
    acl:accessTo <.../rooms/general/>;
    acl:default <.../rooms/general/>;
    acl:mode acl:Read.
```

## What is deliberately NOT on the pod

The boundary:

- **End-to-end encrypted DM content, unless you opt in.** 1:1 DMs are E2E
  encrypted in transit, so by default their plaintext lives only in local device
  storage, not on your pod. There is an **opt-in DM pod archive** (Settings, off
  by default): when you turn it on, your decrypted DM history is written to your
  own pod as `px:Message` JSON-LD under `proxion/dm/`, exactly like room history,
  so it syncs across your devices and any Solid app you authorize can read it.
  This is safe because it is *your* pod holding *your* messages, which you can
  already read; the E2E-in-transit property is unchanged (relays and gateways
  still never see plaintext). The archive is **owner-only**: DM resources inherit
  the `proxion/` container's owner-only ACL, and Proxion never grants member read
  on them (your contact keeps their own copy on their own pod). It stays off
  unless you choose it, because some people do not want conversations on their
  pod at all.
- **Private keys.** Your Ed25519 identity key and message keys never leave the
  device except through the explicit, passphrase-protected recovery kit. DM
  ratchet state is written to `proxion/e2e/<peer>/state-<device>.enc`, encrypted under a
  per-device key that stays in the browser; only the device that wrote it can
  read it. See [E2E_ENCRYPTION.md](E2E_ENCRYPTION.md).

## Reading Proxion data from another Solid app

Everything above is fetchable with a normal authenticated Solid request. A rough
sketch of listing a room's messages from any Solid client:

```js
// `session` is an authenticated Solid session (e.g. @inrupt/solid-client-authn).
const base = "https://alice.pod.example/proxion/rooms/general/messages/";
const index = await (await session.fetch(base + "index.jsonld")).json();
for (const id of index["px:ids"] ?? index.ids ?? []) {
  const msg = await (await session.fetch(`${base}${id}.jsonld`)).json();
  console.log(msg["px:fromName"], msg["px:content"], msg["px:timestamp"]);
}
```

No Proxion code, no gateway, and no Proxion account are involved: it is your data,
in open formats, in your pod.

## Known rough edges

- **Legacy plain-JSON mirror.** Older room message writes also produce a plain
  (non-JSON-LD) `.json` mirror and a `index.json` directly under
  `{pod}/rooms/{roomId}/`. The canonical, documented form is the JSON-LD tree
  under `{pod}/proxion/` described here; the plain mirror is retained for
  backward compatibility and may be consolidated.
- **Vocabulary dereferenceability.** The `https://proxion.dev/vocab/v1#` terms are
  defined by this document; a machine-readable ontology at that URL is planned.

## Stability

The `v1` in the namespace is a promise: within it, terms are added but not
removed or repurposed. A breaking change bumps to `v2` with a documented
migration.

## Browser friendship requests

Browser friendships use two independently owned consent records. Saving a DM
contact does not imply friendship. The browser's Add friend actions send a
request; the recipient must explicitly accept it.

- `proxion/friends/index.json` is owner-only JSON: `{v:1, owner, entries}`.
  Each entry contains a peer WebID and an optional delivery receipt
  `{revision, updated}`. The index currently retains at most 200 peers, including
  historical relationships; removal does not reclaim that capacity.
- `proxion/friends/peers/{sha256(peerWebId)}.json` is writable only by its owner
  and readable by that specific peer. Parent containers remain owner-only.
  WebIDs are normalized with `new URL(webId).href` before hashing.
- `proxion/friend-inbox/` permits public Append and owner Read. Its signed JSON
  snapshots are notification hints, never the authoritative friendship state.

Each peer record contains `v:1`, `actor`, `peer`, `revision`, `updated`, `offer`,
`decision`, and `proof`. Revisions and request IDs are fresh UUIDs; `updated` is
an ISO UTC timestamp. An offer is null or `{id, state}`, with state `pending`,
`withdrawn`, or `removed`. A decision is null or `{id, state}`, with state
`accepted`, `declined`, or `removed`; its ID refers to the other actor's offer.
Friendship requires a pending offer and the other actor's matching acceptance.
Crossed requests still require explicit acceptance. Declines and withdrawals
apply to a particular request ID and cannot cancel a later request.

`proof` is `did:key|base64signature`, signing the ordered fields: domain
`proxion-friend-intent-v1`, version, actor, peer, revision, timestamp, offer, and
decision. Each UTF-8 field has a four-byte big-endian length prefix, with a
literal `|` byte between fields. Non-null slots are JSON strings with `id`
followed by `state`; null slots are empty strings. See
`friendIntentBytes` in `web/dmsig.js` for the exact encoding. Consumers verify
integrity and actor/peer bindings against the fresh canonical resource on the
actor's pod. Ownership of that protected resource is the identity anchor;
requiring the currently published device key would invalidate older records
following a legitimate device change. An inbox snapshot alone is insufficient
proof of consent. Unavailable canonical records do not establish friendship.

Index and intent writes use strong ETags with `If-Match`, or
`If-None-Match: *` on creation. Conflicts are retried with a fresh read; missing
strong validators fail the update. Pending notification delivery survives reload
and retries in the background. Polling supplements resource notifications and
reconciles withdrawals/removals. Removing a friend retains existing DM contacts
and conversation history. Browser friendship requests currently apply between
browser accounts; gateway contact certificates remain a separate mechanism.

## Joined-room membership identity

Each new browser or desktop pod-room approval saves an optional `membershipId`
in its `proxion/joined-rooms.json` record and account-scoped local cache. It is
an opaque fresh ID (at most 128 letters, digits, underscores, or hyphens), retained
across reloads. It distinguishes successive memberships in the same room; it is
not an access credential. The owner's descriptor and pod ACLs still grant access.

Leave and revocation cleanup remove only the owner/container/membership ID they
observed. If another device has saved a newer approval, a conditional-write retry
preserves that newer record. A new local membership also invalidates pending
access checks from the old one. Legacy records without an ID remain readable and
removable; a subsequent approval assigns an ID. Older clients that discard this
field cannot provide the same protection, so all participating clients should
load the updated application.

## Shared pod-room attachments and voice notes

Files up to 2 MiB and WebM voice notes (500–60,000 ms, also at most 2 MiB) are
stored in the **room owner's** Long Chat container. All authorized room members
can upload, read, and download them in browser and desktop pod rooms. These are
plaintext shared-room resources, with the same confidentiality as room messages.
They are separate from the optional legacy gateway copies above.

Clients create `asset-{uploadId}-{chunkIndex}.json` resources with POST to the
room container and a matching Slug. Each document is:

```json
{"v":1,"id":"upload-uuid","index":0,"data":"BASE64_BYTES"}
```

A chunk decodes to at most 32 KiB, with at most 64 chunks per file. The gateway
accepts only this bounded JSON schema on a registered room's upload route and
GET/HEAD on asset paths; it does not expose arbitrary binary fetch, resource
replacement, ACL writes, or deletion through this protocol. Browser clients also
refuse redirected or off-container POST Locations. Created chunks inherit the
room's Read/Append ACL. Members cannot replace them with PUT. Clients verify
changed or corrupted content using the digest below even if storage is modified.

The message content is `[proxion-attachment:v1]` followed by JSON:

```json
{"v":1,"id":"upload-uuid","name":"photo.png","mime":"image/png","kind":"file","size":1234,"sha256":"64_lowercase_hex_digits","chunks":["asset-upload-uuid-0.json"],"spoiler":false}
```

`kind` is `file` or `voice`; voice notes use `audio/webm` and include integer
`durationMs`. Filenames are bounded to 255 characters, MIME types use the app's
file allowlist, and the complete manifest is bounded to 24 KiB. Relative chunk
references resolve only against the actual conversation's vetted owner container;
the reader refuses other paths and duplicate references. The existing Long Chat
proof signs the entire content and its message IRI, binding metadata, chunk order,
and SHA-256 digest to the author and room. Unverified messages cannot load assets.

Uploads confirm each POST and read its bytes back before publishing the signed
manifest. Downloads cap streamed JSON bodies, validate chunk identity/order and
lengths, then verify the complete digest before offering a preview, player, or
file download. Chunks are not loaded automatically from received messages.
A failed download leaves an explicit Retry action.

Failed uploads and unconfirmed manifest writes retain an account/room-scoped draft
in IndexedDB (`proxion-room-media-v1`). At most ten drafts per account are stored.
Drafts contain the original blob, metadata, stable message ID/timestamp/sender name,
and confirmed chunk URLs. Retry after reload reuses verified chunks and the same
message identity; other accounts and rooms do not send that draft. Publishing is
acknowledged only after reading back the verified message from the expected author.
Unsent drafts may be discarded. A changed conversation or session stops further
upload work and keeps its draft in the original room.

Room removal or closure revokes future asset reads and uploads through the
inherited ACL; already downloaded bytes cannot be retracted. The owner retains
messages and assets after room closure. Failed/cancelled uploads can leave
unreferenced chunks in owner storage; the application does not automatically
remove those resources. Message withdrawal removes the visible reference rather
than erasing the owner's stored files.

### Encrypted DM asset storage protocol (UI integration pending)

`web/dm-attachments.js` implements a separate ciphertext-only store at the
sender's WebID-derived `proxion/dm-assets/`. It does not enable the DM attachment
or recording controls. Unlike room assets, DM assets must never contain
plaintext file bytes or a public manifest with a decryption key.

Preparation encrypts the complete file with a fresh AES-256-GCM key and 12-byte
nonce, using the same file crypto as gateway DMs. It produces a structured-clone
compatible object with a ciphertext Blob and private manifest. The caller must
persist this preparation before uploading and preserve it across retries. A new
preparation requires a new random asset ID; it cannot replace the key for chunks
already uploaded under an older ID. The plaintext limit is 2 MiB; the GCM tag
adds 16 bytes, so a maximum-size file uses 65 chunks of at most 32 KiB.

Each chunk lives at `asset-<v4-uuid>-<index>.json` and has exactly four fields:

```json
{"v":1,"id":"018f7291-4cc9-4acf-bdbc-7483881ca971","index":0,"data":"base64_ciphertext"}
```

Writes use `If-None-Match: *`. A 412 response is accepted only after reading back
the existing chunk and verifying its exact ciphertext against the preparation.
Failed or lost responses can therefore retry the same names without replacing
resources. The caller receives completion only after all readbacks pass.

The private manifest starts with `[proxion-dm-attachment:v1]` and includes v1,
asset ID, filename, MIME, plaintext size, kind (`file`/`voice`), chunk count,
ciphertext SHA-256, base64url file key and nonce, spoiler flag, and voice duration
when applicable. It **must travel inside the authenticated encrypted DM body**.
It is not uploaded as a resource. The manifest is capped at 4 KiB and accepts no
URLs; readers derive each chunk path from the authenticated sender's WebID and
validated UUID. Caller-provided `getSelfWebId` allows local pods only on the
current account's pod origin; other private peer hosts are refused lexically.
The desktop bridge also applies its existing DNS/SSRF checks.

Downloads cap JSON streams, enforce the chunk schema, canonical base64, order
and lengths, verify the complete ciphertext digest and GCM tag, and reject
blocked executable signatures before returning plaintext bytes. Invalid content,
failed reads or changed contexts never produce a partially usable file.

The container grants the owner Read/Write/Control and public Read inherited by
chunks. WAC and ACP grant bodies are implemented; WAC is tested against real CSS,
while ACP grants currently have unit coverage. Public reads reveal ciphertext,
random asset IDs, sizes and timing, including the container listing. Filename,
MIME, duration, key and nonce remain in the encrypted DM. These resources are
retained on the sender's pod; removing a contact or DM reference does not revoke
someone who already holds its decryption key. Asset cleanup is still planned.

The desktop bridge allows only this exact resource tree, bounded JSON chunk
PUTs with create-only conditions, and GET/HEAD. Peer reads are unauthenticated;
foreign listings, writes and ACL access are denied. Owner ACL writes are limited
to the exact owner-write/public-read grants, with no public Append or Write.
The existing 64 KiB request and response limits remain unchanged.

Remaining app work: account/conversation-scoped durable draft storage, encrypted
manifest delivery and delivery retries, attachment-card rendering, verified
loads, history restoration and recording controls. Storage-protocol checks do
not substitute for a full chat UI file/voice journey.
