# Round 140: durable encrypted pod DM media

Status: complete for the audited pod-asset routes. Rounds 130–139 remain closed.

Browser and desktop-bridge pod DMs now send uploaded file, voice and forwarded
media manifests through the shared encrypted outbox. Logical ids, sender time,
recipient plans and per-target ciphertext survive failures and reloads; retries
never re-encrypt an existing prepared media send. New device rosters cannot
retarget it. Payload bytes stay in existing IndexedDB drafts and encrypted pod
assets; only the small manifest, frozen envelope and draft reference enter the
outbox. Plaintext fallback is refused.

Media acceptance proof is retained until the draft owner finishes its history
write and removes the draft. A separate durable release flag then permits final
outbox cleanup; unfinished sender mirrors remain independent. A crash after
acceptance but before draft completion finds the same accepted job, and a crash
after draft removal but before release is cleaned idempotently. Missing-draft
checks read only the IndexedDB key, avoiding copies of large media records.

The draft UI distinguishes upload, awaiting confirmation and finishing local
saving. Prepared media cannot be cancelled or have its published assets deleted
through pending-send controls. Pre-upload drafts retain their existing cleanup
controls. Legacy uncertain drafts without a saved envelope require consent to
send a fresh copy, with a fresh id/key/IV and a duplication warning. Already
recorded acceptance can finish history without another delivery.

Media context captures authentication and bridge generations as well as actor
and tab ownership. Replacement credentials, including switching away and back
to the same pod, invalidate delayed work. Saved media from another pod account
stays blocked instead of being signed under the replacement account. The pending
list links confirmed media to its original conversation for local completion.

Scope audit: this closes the existing pod-asset routes exposed by browser DMs,
desktop-to-WebID DMs and their file/GIF/voice/private-forward controls. Browser
pairing and multi-device WebID consumption remain unsupported, so those controls
are not introduced. Per-device media routing is covered through the shared
outbox/frozen-plan tests; there is no supported live paired-browser media route.
Native gateway inline files and the separate file_offer/file_chunk transfer
protocol retain their existing behavior and do not enter this manifest adapter.
Their offline/reload durability requires separate secondary-envelope/transfer
work, rather than a hidden rewrite of that protocol in this round.

Validation: 2,567 frontend checks passed, with 64 optional integration skips. ESLint, whitespace, i18n and cold-path gates pass (15 existing draft
locale warnings; English fallback and pseudo-locale updated). Live browser and
both desktop-bridge directions pass upload/delivery failure and lost acceptance
write recovery, identical saved ciphertext after reload, safe outbox release,
exact restored bytes, real recorded voice playback, fresh-key forwarding,
unused-upload cleanup interruption, and existing text/receipt/block journeys.
Shell cache: v303. Backend schema/protocols are unchanged.
