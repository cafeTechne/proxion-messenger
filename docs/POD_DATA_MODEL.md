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
  forwards them can read them. This is a property of *delivery*.
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

Attachments and voice notes are stored as ordinary files with their real content
types, so any app or file browser can open them directly:

- Voice notes: `proxion/rooms/{roomId}/files/{messageId}.webm` (`audio/webm`)
- File attachments: `proxion/rooms/{roomId}/files/{messageId}/{filename}`
- Avatar: `proxion/profile/avatar.png`

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

Being honest about the boundary matters more than a tidy story:

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
  device except through the explicit, passphrase-protected recovery kit.

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

In the spirit of an honest spec rather than a marketing one:

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
