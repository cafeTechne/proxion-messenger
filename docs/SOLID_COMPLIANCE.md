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
| Web Access Control | v1.0.0 | Compliant (structure + header discovery) | yes (B3) |
| Access Control Policy | v0.9.0 | Partial (authored + routed; unverified vs live ESS) | yes (B3) |
| Authorization Use Cases | ED | N-A (informational) | yes (B3) |
| Solid Notifications Protocol | v0.3.0 | Compliant (+ polling fallback) | yes (B4) |
| WebSocketChannel2023 / WebhookChannel2023 / LDNChannel2023 | ED / v1.0.0 | Compliant | yes (B4) |
| StreamingHTTPChannel2023 | ED | Missing (minor) | yes (B4) |
| EventSourceChannel2023 | ED | N-A (WebSocket covers it) | yes (B4) |
| Solid-PREP | ED | Missing (emerging) | yes (B4) |
| Solid Application Interoperability (+ primers) | v0.1.0 | Missing (emerging; depends on Shape Trees) | yes (B5) |
| Solid QA | v0.3.0 | N-A (process); our test discipline aligns | yes (B5) |
| Solid Security Considerations | v0.1.0 | Aligned | yes (B5) |
| Solid ERP | WIP | Note only | yes (B5) |

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

## B3. Authorization

### Web Access Control (v1.0.0)
| Requirement | Status | Evidence / note |
|---|---|---|
| `acl:Authorization` with `acl:agent` / `acl:agentClass foaf:Agent` (public) | Compliant | `buildWacAcl` (`pod.js`); public read on the type index |
| `acl:accessTo` / `acl:default` (container inheritance) | Compliant | container ACLs use `acl:default`; rooms grant members |
| `acl:mode` Read/Write/Append/Control; owner keeps Control | Compliant | owner Read/Write/Control on every resource we create |
| Serve ACL as `text/turtle` | Compliant | all ACL writes are turtle |
| Discover the ACL URL from `Link: rel=acl` (never derive by string) | Compliant | R100 A2.1 + R101.2: every ACL write site (`podSetContainerAcl`, `podGrantChatParticipants`, `ensureProxionContainer`, type index, inbox) reads the `Link` header via `discoverAccessControl` (`acl.js`), falling back to `.acl` only when the server advertises nothing. |

### Access Control Policy (v0.9.0)
| Requirement | Status | Evidence / note |
|---|---|---|
| Author Access Control Resources (Policies + Matchers) to grant access | **Partial** | R100 A2.2: `buildAcpAcr` authors an ACR (AccessControl + Policy + Matcher, owner control + member read, with `acp:memberAccessControl` for inheritance), and `podSetContainerAcl` routes to it when the server advertises ACP. Structurally unit-tested. **NOT yet verified against a live Inrupt ESS**, so treat as best-effort until tested. Only activates on ACP servers, so no CSS risk. |
| Discover the ACR via the resource's `Link` header | Compliant | `detectAclModel` / `accessControlUrl` handle the ACP accessControl relation (A2.1) |

### Authorization Use Cases and Requirements (Editor's Draft)
| Requirement | Status | Evidence / note |
|---|---|---|
| (requirements/use-cases document, not client-normative) | N-A | informational; nothing to implement |

## B3 verdict
WAC is structurally compliant, with one real conformance gap (ACL discovery by `.acl`
convention rather than the `Link: rel=acl` header). ACP is the biggest ecosystem gap and is
the R100 A2 item: supporting Inrupt ESS needs BOTH ACP authoring AND header-based ACR
discovery, not `.acl` guessing. This sharpens A2's scope.

## B4. Notifications

### Solid Notifications Protocol (v0.3.0)
| Requirement | Status | Evidence / note |
|---|---|---|
| Discover the subscription service via storage description / `describedby` | Compliant | reads the storage description; speaks v0.3 directly (not `@inrupt/solid-client-notifications`, which mis-detects CSS 7). See `NOTIFICATIONS.md` |
| POST a subscription (`application/ld+json`, `topic`, channel type) | Compliant | `notify.js`, `notify.test.js` |
| Receive Activity Streams notifications | Compliant | `podcanonical-notify-live` |
| Polling fallback | Extension | the spec defines none; Proxion adds polling for servers offering no service (behind-NAT / no-push) |

### Notification channels
| Channel | Status | Evidence / note |
|---|---|---|
| WebSocketChannel2023 | Compliant | live-update subscription for new posts |
| WebhookChannel2023 | Compliant | closed-app path: Webhook to gateway to Web Push (`NOTIFICATIONS.md`) |
| LDNChannel2023 (LDN inbox) | Compliant | inbox invitations produced/consumed (`ldn.js`, `podcanonical-ldn-live`) |
| StreamingHTTPChannel2023 | Missing | newer channel CSS is adopting; minor follow-up if CSS deprecates WebSocket |
| EventSourceChannel2023 | N-A | WebSocket covers the same need |

### Solid-PREP (Editor's Draft)
| Requirement | Status | Evidence / note |
|---|---|---|
| Per-resource events via Fetch (lightweight SNP complement) | Missing | emerging (new-work-item stage); our SNP WebSocket + Webhook + poll already cover real-time. Watch, do not build yet. |

## B4 verdict
Notifications is a strong, spec-conformant area: v0.3 discovery + WebSocket + Webhook + LDN,
plus a polling fallback the spec does not require. Only StreamingHTTPChannel2023 (minor) and
the emerging Solid-PREP are unimplemented; neither is a current gap.

## B5. Interoperability and process

### Solid Application Interoperability (v0.1.0, + primers)
| Requirement | Status | Evidence / note |
|---|---|---|
| Registry set from WebID; Data Registrations by shape tree; Access Need Groups; Authorization Agent; Access Grants | Missing | Proxion has a gated-off `@inrupt/solid-client-access-grants` adapter (VC access grants) but implements none of the SAI panel model, which also depends on Shape Trees (also Missing). Large and still evolving (CG draft). Watch; revisit when it stabilizes. |

### Solid Security Considerations (v0.1.0)
| Recommendation | Status | Evidence / note |
|---|---|---|
| DPoP-bound tokens | Aligned | `dpop.py` |
| Trust anchors: verify OIDC issuer; do not let apps rewrite WebID cards | Aligned | issuer handled by `solid-client-authn`; we only augment our own card, never others' |
| Treat pod data as untrusted; sanitize rendered content | Aligned | markdown/render escaping; XSS-sink audit hardened `innerHTML` uses |
| Protect credentials / private keys | Aligned | non-extractable device keys (WebCrypto); updater key git-excluded |
| CSP sandbox for pod-served HTML | N-A | Proxion renders pod content as escaped text, it does not serve pod-hosted HTML |
| Origin checks on privileged endpoints | Aligned | `/setup/pod` rejects untrusted origins (`test_security_hardening.py`) |

### Solid QA (v0.3.0) and Solid ERP (WIP)
| Item | Status | Evidence / note |
|---|---|---|
| Solid QA (test/conformance process) | N-A (process) | not a client-normative spec; our unit + e2e + live-CSS integration + smoke gates align with its intent |
| Solid ERP | Note only | early/work-in-progress; nothing to implement |

## B5 verdict
Interop-and-process is either intentionally deferred (SAI, which is emerging and Shape-Tree
dependent) or already aligned (Security Considerations) or non-normative (QA, ERP). No new
actionable gaps.

---

## Audit complete: overall picture

Proxion is **substantially spec-conformant** on the parts of Solid it uses: Protocol CRUD,
Long Chat, Type Indexes, WebID Profile, Solid-OIDC + DPoP, WAC (structure), and the
Notifications Protocol with WebSocket/Webhook/LDN channels plus a polling fallback. The
gaps are a small, honest set, and several are by design.

### Actionable follow-ups (ranked)
1. **Verify ACP against a live Inrupt ESS** (A2): header-based discovery (A2.1) and ACP ACR
   authoring (A2.2) are implemented and structurally tested; the remaining step is running a
   real ESS grant/read to confirm the ACR shape, then flipping ACP from Partial to Compliant.
   Needs an Inrupt PodSpaces account.
2. **Long Chat replies + reactions in standard predicates** (B1): also emit
   `sioc:has_reply` and `schema:Action` so other Solid apps see threading and reactions.
   Cheap, high interop value.
3. **PATCH via N3 Patch (or Accept-Patch negotiation)** (B1): for servers that do not accept
   SPARQL Update. (R101.3)
4. **StreamingHTTPChannel2023** (B4): minor; add if CSS deprecates WebSocket. (R101.4)

Done since the audit: header-based ACL discovery at every write site (R101.2).

### Deferred by design / emerging (watch, do not build)
did:solid alignment, SAI + Shape Trees, Solid-PREP, HTTPSig. did:key-only users having no
dereferenceable card is a stated design limit.

## B2 verdict
Identity and auth are compliant for pod-connected users (browser Solid-OIDC + DPoP, WebID
card augmented with name/inbox/type-index). The one honest limit is by design: a did:key-only
user has no dereferenceable card, and Proxion's did:key identity diverges from the emerging
did:solid method. No new actionable gaps beyond that strategic choice.

_(B3 authorization, B4 notifications, B5 interop/process to follow.)_
