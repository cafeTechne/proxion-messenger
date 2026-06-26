# Proxion: Abstract and Existential Instantiation Notes

## Abstract

Proxion defines a universal, tool‑agnostic architecture for privacy‑preserving device‑to‑device connection bootstrapping via a dedicated control plane. The architecture combines interactive authorization (UMA‑style), capability tokens with contextual caveats (macaroon‑style), delegation and revocation (CapBAC), and decentralized service discovery, while explicitly separating authorization and negotiation from data transport. Normative invariants guarantee least authority, single‑use bootstrapping, finite authority, contextual enforcement, audience binding with proof‑of‑possession, and the absence of any requirement for globally stable identifiers.

A minimal reference implementation (EI₀) demonstrates the existence of a system satisfying these invariants without prescribing identity systems, transports, or user interfaces. By decoupling governance from payload movement, Proxion admits diverse instantiations—from local pairing to secure channel bootstrapping—while preserving strong privacy properties and fail‑closed semantics. This work positions Proxion as a unifying control‑plane pattern applicable across Solid‑style governance, capability systems, and secure channel establishment.

---

## Standalone EI Witness — QR Bootstrap (Non‑Normative)

> This document describes a **concrete existential instantiation (EI)** of the Proxion Universal Architecture. It is informative only and imposes no requirements on the UI.

### Scope

* Demonstrates ticket‑based bootstrapping using an out‑of‑band handle.
* Uses a QR code solely as a transport for the bootstrap handle.
* Keeps authorization, claims, and policy in the Control Plane (CP).
* Treats the Data Plane (DP) as an opaque secure channel bootstrap.

### Actors

* **RO**: Human approving a connection.
* **RP**: Device requesting access.
* **AS/RS**: Authorization and Resource services implementing Proxion UI semantics.

### Flow Summary

1. **Ticket Issuance (RO → AS)**

   * RO requests a connection intent.
   * AS issues a short‑lived, single‑use Permission Ticket (PT).
   * RO encodes the PT and AS endpoint into a QR payload.

2. **Ticket Redemption (RP → AS)**

   * RP scans QR and redeems PT.
   * AS gathers claims and evaluates policy.
   * AS issues a capability token (RPT/CT) with explicit permissions and caveats.

3. **Data Plane Bootstrap (RP → RS)**

   * RP presents the token to RS.
   * RS validates permissions, caveats, audience, PoP, freshness, and revocation.
   * RS returns DP connection material (opaque to the UI).

### Security Notes

* QR leakage is mitigated by PT single‑use + TTL.
* Replay is mitigated by audience binding and PoP.
* Over‑permission is prevented by explicit enumeration and attenuation.

---

## Separate Architecture Note — Solid ↔ Secure‑Channel EI (Informative)

### Goal

Sketch an EI that bridges **Solid governance** with **secure channel establishment** without coupling either system into the UI.

### Architectural Roles

* **Solid Pod**: Persistence and governance layer (policies, consent receipts, optional audit logs).
* **Proxion Control Plane**: Pairing, negotiation, delegation, and capability issuance.
* **Secure Channel DP**: Independently defined mechanism (e.g., tunnel or encrypted transport).

### Interaction Pattern

1. RO stores policies and optional claims in a Solid Pod.
2. Proxion AS queries the Pod during authorization (read‑only, policy‑scoped).
3. AS issues a capability token authorizing *channel establishment*, not data access.
4. RP and RS establish a secure channel using any compliant DP.

### Key Properties

* Solid is **optional** and not required for runtime pairing.
* The secure channel is **opaque** to the Control Plane.
* Revocation and attenuation occur at the CP level.
* Privacy is preserved via pairwise identifiers and minimized tokens.

### Non‑Goals

* No mandate on specific tunnel protocols.
* No requirement that Solid pods be online during data transfer.
* No embedding of transport identifiers into policy semantics.

---

## Status

This document complements the frozen Proxion UI and EI₀ (v0.1.0) and serves as a reference for future existential instantiations. It does not introduce new requirements.
