# Easy Federation User Flow

This document describes the step-by-step flow for non-technical users to connect
with another Proxion user across different gateways.

---

## Prerequisites

- Proxion installed and running (desktop app or local server).
- Internet access (direct UDP preferred; HTTP relay fallback automatic).

---

## Steps

### 1. Enable Easy Federation

Open Settings → Privacy → Enable Easy Federation.

The gateway generates a WireGuard overlay identity (Curve25519 keypair stored
locally). No router configuration, port forwarding, or DNS setup required.

### 2. Get Your Connect ID

Click **Share My Address** (or **Copy Connect ID**).

A compact Connect ID is displayed:

```
proxion:H4sI...#a3f2
```

This string encodes your DID and gateway URL — no external coordinator is used.
Send it to your contact via any channel (SMS, email, QR code).

### 3. Accept a Contact Request

Your contact pastes or scans your Connect ID → clicks **Add Contact**.

The gateway decodes the Connect ID, resolves your DID and gateway URL, and
initiates the existing federation handshake (mutual RelationshipCertificate
exchange).

### 4. Check Connection Status

A plain-language label appears in Settings → Connectivity:

| Label | Meaning |
|---|---|
| **Private direct connection** | WireGuard direct UDP path — best privacy and latency |
| **Private relayed connection** | HTTP relay fallback — still E2E encrypted, no message content visible to relay |
| **Needs attention** | Check internet connection or firewall |

### 5. Chat and Voice

Messages and voice calls work automatically. No additional configuration needed.

---

## Privacy Notes

- **Direct path**: Only your gateway and your contact's gateway are involved. No third party sees traffic.
- **Relay path**: HTTP relay sees encrypted ciphertext only (sealed sender, e2e_v=3). Relay cannot read message content or determine sender identity.
- **Connect ID**: Self-contained — contains your DID and gateway URL. No central resolver; no tracking.

---

## Troubleshooting

| Symptom | Action |
|---|---|
| Contact cannot resolve Connect ID | Re-share a freshly generated Connect ID |
| Always on relayed connection | Check that UDP port 51820 is reachable from the internet |
| "Needs attention" | Verify internet connectivity; check Connectivity Diagnostics in Settings |
