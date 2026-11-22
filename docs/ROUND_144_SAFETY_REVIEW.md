# Round 144: retirement safety review

Status: design-only outcome. No prepared cancellation or ratchet-reset protocol
is enabled. This review does not close the remaining media implementation work
in Rounds 142–143.

A ratchet encryption advances a particular peer/device chain even if the job's
prepared-frame write fails. The atomic send journal is therefore evidence of a
spent position; absence of a frame in the outbox is not evidence that no key was
spent. Removing successive such intents can leave the receiver more than the
existing MAX_SKIP (20) positions behind. Increasing that limit or resealing the
same logical message does not establish safe retirement.

| Saved state | Allowed action | Evidence and limit |
| --- | --- | --- |
| Text intent, no frame, no pending send on any frozen target | Cancel | Serialized cancellation checks the send journal again before removing the job; an account/generation change aborts it. No ratchet position has been spent. |
| Preparation interrupted after a journal write | Exact retry | The original target/send id restores the original header. Missing frames do not permit cancellation. |
| Prepared, no acceptance | Retain and retry original destination | Lack of acceptance is not proof of non-delivery or a safe chain gap. |
| Some devices accepted | Retain unfinished targets | Accepted positions are scoped to those devices' independent chains. They do not prove that an unaccepted device advanced. |
| Peer accepted, sender mirror blocked/revoked | Keep peer confirmed; retain mirror for original-device recovery | Revocation prohibits sending, but proves nothing about that mirror's last consumed position. It cannot justify silently discarding its prepared key. |
| All frozen targets accepted | Existing cleanup | Durable per-target acceptance permits dropping the local send journal, after required history/media cleanup. This is completion, not cancellation or recall. |
| Original account unavailable / legacy actor unknown | Original-account sign-in or explicit text review | Never assign a replacement publisher to an already prepared job. A manual new send has a new id and can duplicate a previously received message. |
| Media / possibly published asset | Retain existing ownership hold | A text/device receipt cannot authorize deletion of a media asset another device might still need. |

The sender outbox still receives gateway acceptance, not a peer-signed chain
checkpoint. The new receive-commit reply is addressed to the receiving client.
The new gateway receive-commit receipt binds an exact envelope, authenticated
recipient account and device. It proves consumption of that envelope under the
client's local retention policy. It is not a peer-authenticated ratchet position
checkpoint and cannot certify unrelated chains, keys, earlier dropped messages
or unaccepted targets. A device being permanently revoked does not reset its
old chain.

No additional safe automatic retirement follows from the current evidence.
Unsafe actions stay unavailable in pending-send controls. Recover the original
account/device or restore the frozen target's key; do not retarget prepared
ciphertext. Clearing all browser data is destructive local data loss, can lose
keys/history and pending sends, and is not recall or a supported queue-repair
button. No new reset control is shipped.

A future retirement protocol requires a separately reviewed, versioned design
binding peer and chain identity, authenticated checkpoint positions, replay and
ordering rules, supported peers, outstanding media holds, and both sides of
crash/commit boundaries. Until then, the existing skip bound, key non-reuse and
recipient binding remain unchanged.

Validation: outbox coverage exercises interrupted preparation, cancellation
racing encryption, changed/revoked frozen devices, original-account switching,
partial device acceptance, and confirmed peer/unconfirmed mirror recovery.
The multi-device live journey checks that a confirmed peer stays confirmed while
only the exact unfinished mirror retries after browser/gateway restart. No new
retirement action is claimed, so there is no new crypto safety assumption.
