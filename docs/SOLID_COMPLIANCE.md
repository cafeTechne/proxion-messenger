# Solid specification compliance

An honest, cited map of Proxion against the Solid specification suite
(solidproject.org/TR). Each row is a client-facing requirement, its status, and the
code or test that proves it. Statuses:

- **Compliant**: implemented and tested.
- **Partial**: works against our primary target (CSS) but not fully to spec or not on all servers.
- **Missing**: not implemented.
- **N-A by design**: intentionally not done (stated, not a gap).

Proxion is a client to the pod (plus its own relay); server-only obligations are out of
scope. Reaching 100% is not the goal; an honest, cited picture is.

## Summary (per spec)

| Spec | Version | Overall | Audited |
|---|---|---|---|
| Solid Protocol | v0.11.0 | Compliant (core), Partial (PATCH format, ACP) | yes (B1) |
| Solid Chat (Long Chat) | v1.0.0 | Compliant (core), Partial (replies, reactions) | yes (B1) |
| Type Indexes | v1.0.0 | Compliant (public); private index N-A | yes (B1) |
| Shape Trees | ED | Missing (low priority) | yes (B1) |
| Solid WebID Profile | v1.0.0 | Compliant (pod users); N-A for did:key-only users | yes (B2) |
| Solid-OIDC (+ Primer) | v0.1.0 | Compliant | yes (B2) |
| HTTPSig Authentication | CG-draft | N-A by design (we use Solid-OIDC/DPoP) | yes (B2) |
| Solid DID Method (did:solid) | Unofficial | Divergent by design (we use did:key) | yes (B2) |
| Web Access Control | v1.0.0 | pending | B3 |
| Access Control Policy | v0.9.0 | pending (known gap, R100 A2) | B3 |
| Authorization Use Cases | ED | pending | B3 |
| Solid Notifications Protocol | v0.3.0 | pending | B4 |
| WebSocket/Webhook Channel 2023 | ED | pending | B4 |
| StreamingHTTP/EventSource/LDN Channel 2023 | ED | pending | B4 |
| Solid-PREP | ED | pending | B4 |
| Solid Application Interoperability (+ primers) | v0.1.0 | pending (adapter gated off) | B5 |
| Solid QA | v0.3.0 | pending | B5 |
| Solid Security Considerations | v0.1.0 | pending | B5 |
| Solid ERP | WIP | note only | B5 |

## B1. Core and data

### Solid Protocol (v0.11.0)
| Requirement | Status | Evidence / note |
|---|---|---|
| Content-Type on PUT/POST/PATCH | Compliant | `web/pod.js` sets it on every write |
| GET/HEAD for reads, PUT/POST/PATCH/DELETE for writes | Compliant | `pod.js`, `solid_client.py` |
| Discover storage root (pim:storage / Link rel type Storage) | Compliant | `podStorageRoot`, storage description read for notifications |
| text/turtle + application/ld+json | Compliant | reads/writes both |
| PATCH format is N3 Patch (server advertises text/n3 in Accept-Patch) | **Partial** | Proxion sends `application/sparql-update` (works on CSS). A server that only accepts N3 Patch would reject our profile/type-index patches. Follow-up: send N3 Patch, or content-negotiate on Accept-Patch. |
| Container creation | Compliant | PUT creates intermediate containers on CSS |
| Auth: Solid-OIDC / WebID-TLS | Compliant | browser uses `@inrupt/solid-client-authn` (Solid-OIDC); gateway uses Solid-OIDC client-credentials + DPoP |
| Access control: WAC or ACP | **Partial** | WAC only; ACP not written (R100 A2, blocks ESS) |

### Solid Chat / Long Chat (v1.0.0)
| Requirement | Status | Evidence / note |
|---|---|---|
| `meeting:LongChat` channel resource | Compliant | renders in SolidOS: `web/solidos-render.test.js` |
| `dct:created`, `sioc:content`, `foaf:maker` (all mandatory) | Compliant | `pod.js` message writer; `INTEROP.md` |
| Channel to message link (`meeting:message` + `wf:message`) | Compliant | both emitted (SolidOS reads `wf:message`) |
| Date-partitioned message files | Compliant | `podWriteLongChatMessage` layout |
| Deletes (`schema:dateDeleted`) | Compliant | tombstone emitted; renders in SolidOS |
| Edits | Compliant | in-place `sioc:content` swap (`buildEditPatch`), so any Long Chat reader shows the latest text; chosen deliberately over `dct:isReplacedBy` (unverified reader support), with `px:` keeping full history. `solidos-render-edits.test.js` |
| Replies via `sioc:has_reply` | **Partial** | Proxion stores reply context in `px:`, so other apps do not see threading. Follow-up: also emit `sioc:has_reply`. |
| Reactions via `schema:Action` subclasses | **Partial** | Proxion stores reactions in `px:`, so other apps do not see them. Follow-up: also emit `schema:LikeAction` etc. |

### Type Indexes (v1.0.0)
| Requirement | Status | Evidence / note |
|---|---|---|
| Link `solid:publicTypeIndex` from the WebID card | Compliant | `podEnsurePublicTypeIndex`, `typeindex.test.js` |
| Register `solid:TypeRegistration` + `solid:forClass` + `solid:instanceContainer` | Compliant | `podRegisterChat`, `podcanonical-typeindex.test.js` |
| Discover instances from another agent's public index | Compliant | `podReadPublicTypeIndexUrlFor`, `discover.test.js` |
| Private type index (`solid:privateTypeIndex`) | N-A by design | Proxion publishes only shared chats to the public index; it keeps no discoverable private-data index |

### Shape Trees (Editor's Draft)
| Requirement | Status | Evidence / note |
|---|---|---|
| Shape Tree locators / data organization by shape | Missing | Not implemented. Low priority: emerging spec, little adoption; our data is already typed RDF other apps read. |

## B2. Identity and authentication

### Solid WebID Profile (v1.0.0)
| Requirement | Status | Evidence / note |
|---|---|---|
| Card is `foaf:Agent`, GET as turtle/JSON-LD, `pim:preferencesFile` | Compliant | CSS/JSS create the card; we read/augment it |
| `solid:oidcIssuer`, `pim:storage` present | Compliant | provided by the pod's default card; not clobbered |
| `foaf:name` so other apps show a name | Compliant | R100 A1 upsert (`podEnsureProfileName`); `profile-card.test.js` |
| `ldp:inbox` advertised | Compliant | we create + link an inbox for LDN invites (`inboxacl`, `ldn.js`) |
| `solid:publicTypeIndex` linked | Compliant | `podEnsurePublicTypeIndex` (see B1) |
| A did:key-only user is discoverable/named | N-A by design | no pod means no dereferenceable card; such a user shows as an id (documented in `INTEROP.md`) |

### Solid-OIDC (v0.1.0)
| Requirement | Status | Evidence / note |
|---|---|---|
| Auth Code + PKCE, `webid` claim (interactive) | Compliant | browser uses `@inrupt/solid-client-authn` (`solid-authn.bundle.js`) |
| DPoP-bound tokens (required) | Compliant | `dpop.py`; DPoP on pod I/O |
| Headless/backend agent auth | Compliant | gateway uses Solid-OIDC client credentials against CSS/JSS (`css_auth.py`, `jss_setup.py`) |
| Client Identifier Document | N-A | registrationless/ephemeral client (a spec-endorsed option); no hosted Client ID doc |

### HTTPSig Authentication (CG-draft)
| Requirement | Status | Evidence / note |
|---|---|---|
| HTTP Message Signatures auth | N-A by design | Proxion uses Solid-OIDC + DPoP, the mainstream path; HTTPSig is an alternative not needed |

### Solid DID Method / did:solid (Unofficial Draft)
| Requirement | Status | Evidence / note |
|---|---|---|
| `did:solid` identity resolving to a WebID profile | Divergent by design | Proxion identity is `did:key` (self-certifying, no resolution). `did:solid` is an Unofficial Draft; aligning would be a future identity-model decision, not a current compliance gap. Spec issue solid/specification#217 tracks DIDs-alongside-WebIDs. |

## Follow-ups surfaced by B1
1. **PATCH format** (Protocol): our writes use `application/sparql-update`; add N3 Patch (or Accept-Patch negotiation) for servers that do not accept SPARQL Update.
2. **Long Chat replies** (Chat): also emit `sioc:has_reply` so reply threading is visible to other Solid apps.
3. **Long Chat reactions** (Chat): also emit `schema:Action` subclasses so reactions are visible to other Solid apps.
4. **ACP** (Protocol auth): the known R100 A2 item (blocks Inrupt ESS).

## B2 verdict
Identity and auth are compliant for pod-connected users (browser Solid-OIDC + DPoP, WebID
card augmented with name/inbox/type-index). The one honest limit is by design: a did:key-only
user has no dereferenceable card, and Proxion's did:key identity diverges from the emerging
did:solid method. No new actionable gaps beyond that strategic choice.

_(B3 authorization, B4 notifications, B5 interop/process to follow.)_
