# Interoperability and server compatibility

This is the honest, specific picture of what Proxion interoperates with, how, and
where the edges are. Every capability listed here is backed by a test in the repo,
named so you can run it yourself.

## What interoperates, and what does not

- **Rooms and shared conversations interoperate.** They are shared by design, so
  their history is plaintext, standard RDF that any authorized Solid app can read and
  write.
- **Direct messages do not, on purpose.** DMs are end-to-end encrypted. You cannot
  have bytes a third-party app can read and that no third party can read, so DMs stay
  Proxion-only and are deliberately not third-party readable. This is a design choice,
  not a gap.

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
  databrowser, messages and authors intact, including edits and deletes.
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
| Community Solid Server 7.x | Yes | Yes (WebSocketChannel2023, v0.3) | WAC | Primary target, fully exercised in tests |
| solidcommunity.net (CSS) | Yes | Depends on deployment config | WAC | Same engine as above |
| Inrupt PodSpaces (ESS) | Read/write expected (standard RDF) | Not via our client path | ACP | See caveats below |

Caveats worth stating:

- **Notifications: v0.3 protocol, spoken directly.** We do not use
  `@inrupt/solid-client-notifications`. Verified live, that library (v3) looks for an
  old-style negotiation gateway and reports CSS 7 as unsupported, while CSS advertises
  a subscription service in its storage description (the v0.3 model). Proxion speaks
  v0.3 directly, and falls back to polling on any server that offers no service.
- **Access control: WAC, not ACP.** Proxion writes Web Access Control ACLs. Servers
  that use only Access Control Policy (ACP) are not targeted for the participant-grant
  flow yet.
- **Identity: a pod-less user shows as an id.** A `did:key`-only identity has no
  dereferenceable WebID card, so other apps display the identifier rather than a name.
  That is the expected limit of interop for users who have not connected a pod.

## Order across devices and members

Message order uses a per-message `px:seq` (the gateway's single-clock time) when
present, falling back to timestamp otherwise, so a user's devices, and members of a
single-pod chat, agree on order despite client clock skew.
`web/longchat-order.test.js`, `web/podcanonical-d4.test.js`.
