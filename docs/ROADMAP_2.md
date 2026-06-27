# Proxion Messenger — Roadmap 2: From Functional to Delightful

`docs/ROADMAP.md` (Phases A–E) got the product **feature-complete and
architecturally sound**: download-and-go distribution, relay reachability, the
three monoliths decomposed, federation completeness, and most of social parity.
Phases A–D are done; E (durability) remains.

This second roadmap is about a different axis: **turning a feature-rich tool into
a polished, trustworthy, obviously-usable product a non-technical person loves.**
The thesis is unchanged — *download a `.exe`, share a link, talk to your friends*
— but the bar moves from "it works" to "it feels like a modern app and never
makes me feel lost or unsure."

Written after a full read of the web client and a session that decomposed and
hardened the backend. The findings below are specific and measured, not vibes.

---

## Current state — honest assessment

**Genuinely strong**
- Deep feature set: rooms, DMs, reactions, edits, threads, pins, disappearing
  messages, scheduling, search, receipts, presence, typing, voice, files, push.
- Real test discipline now: 222 web unit tests + 3,235 backend tests, two
  headless smoke harnesses (`smoke:browser`, `smoke:webrtc`), 31 ES modules.
- Responsive shell (`@media` mobile/desktop), `prefers-reduced-motion`, sensible
  feedback (67 `showToast` sites, 72 error/catch sites, empty states).
- No `TODO/FIXME/BROKEN` debt markers in app code.

**Where it falls short of "delightful & trustworthy"** (all measured)
1. **Visual/design debt.** 343 inline `style=` attributes in `index.html`; 51
   distinct hardcoded hex colors against only 8 design tokens; CSS split between
   a 444-line inline `<style>` block and `style.css`. There is a token system —
   it's just barely used. The UI is functional but inconsistent and hard to
   re-theme or keep visually coherent.
2. **Keyboard & focus accessibility.** Essentially **no `:focus` styles** (2
   total) — keyboard users can't see where they are. `aria-*` is present (38) but
   focus management is minimal (1 `tabindex`); never audited against WCAG.
3. **Cognitive load.** 32 settings controls and 12 modals — enterprise-grade
   surface area for a "noobie" app. Onboarding still surfaces Solid-Pod/CSS
   concepts in its default path (the A3.2 follow-up).
4. **Half-built features that look present.** Room **voice channels** are a UI
   stub (`updateVoiceChannels` is a no-op; the group-voice signaling exists but
   is unreachable from the UI). Voice UX is thin: no speaking/level indicators
   (you can't tell who's talking), no call-quality stats, no push-to-talk (the
   D1 follow-ups).
5. **Reliability blind spots.** This session found and fixed *real* latent bugs
   that no test caught — `_write_json`/`_IMPORT_MAX` `NameError` landmines in
   cold HTTP error paths, a ws/wss mixed-content failure that silently broke
   connection, and an empty-room-list-after-restart bug. Cold/error paths are
   under-exercised, and there is no automated full *user-journey* E2E (the smoke
   tests catch eval errors, not "can a user actually complete a flow").
6. **Invite/onboarding gaps.** No gateway-served landing page for `/i/<token>`
   so a not-yet-installed recipient is left at a raw endpoint (the A4.1
   follow-up); the carry-through is client-only.
7. **Mobile is responsive, not native-feeling.** One breakpoint; no installable-
   PWA polish or mobile-first interaction patterns (the D2 item).

---

## Phase F — Visual coherence & a real design system  (highest leverage)

A product feels trustworthy before a user reads a word. Pay down the visual debt.

- **F1. Token-driven styling.** Promote the 51 ad-hoc colors + spacing/radii/
  typography into the existing CSS-custom-property system; expand from 8 tokens
  to a real scale (color, space, font-size, radius, shadow, z-index). One source
  of truth; dark theme stays a token flip.
- **F2. De-inline the UI.** Migrate the 343 inline `style=` attributes into
  semantic classes; consolidate the 444-line inline `<style>` block and
  `style.css` into one structured stylesheet. This is mechanical and unlocks
  consistency + theming + smaller HTML.
- **F3. State design.** A deliberate pass on empty / loading / error / offline
  states — skeleton loaders, friendly copy, illustrations. Replace ad-hoc
  "Loading…" strings with a consistent pattern.
- **F4. Micro-interaction polish.** Consistent transitions, the reaction-pop
  pattern (R48) extended tastefully, message-send optimism, scroll behavior — all
  behind `prefers-reduced-motion`.

## Phase G — Usability & cognitive-load reduction

Make the obvious thing obvious; hide power behind progressive disclosure.

- **G1. Settings triage.** Split the 32 controls into a short default set + an
  "Advanced" section. A noobie should see ~5 things.
- **G2. Onboarding finish (A3.2 + A4.1).** Default first-run with zero
  Pod/CSS/gateway language; Pod connect behind an optional Settings affordance; a
  gateway-served `/i/<token>` landing that branches installed-vs-download and
  carries the inviter through.
- **G3. Discoverability.** Contextual tooltips, empty-state CTAs ("Add your first
  friend"), a command palette (the shortcut system exists), and first-use
  coachmarks. Reduce modals — prefer inline/side-panel flows.
- **G4. Reduce dead ends.** Audit every error/empty state for a next action.

## Phase H — Reliability & quality hardening

Trust is lost in the cold paths. Make the latent-bug class systematically
impossible.

- **H1. Cold-path bug sweep + CI guard.** The `_write_json` class (names
  referenced but never defined, only reachable in error branches) was invisible
  to lint and tests. Add the scope-aware AST free-name check (already prototyped
  this session) as a CI gate for both Python and JS; sweep error/recovery paths.
- **H2. Real user-journey E2E.** Grow the puppeteer harness from "page loads
  cleanly" to driving actual flows (send/receive, react, reply, edit, room
  create/join, settings) with assertions — and a **two-gateway federation
  harness** for the cross-gateway DM/voice paths that are currently manual.
- **H3. Visual-regression snapshots** on key screens so the Phase F refactor and
  future changes don't silently break layout.
- **H4. Graceful degradation audit** — every external dependency (pod, relay,
  TURN, push) failing should degrade visibly, never silently.

## Phase I — Accessibility & inclusivity

- **I1. WCAG 2.1 AA pass.** Visible focus (`:focus-visible`) everywhere; full
  keyboard navigation; SR labels/roles audit; contrast-check the color set
  (ties to F1); reduced-motion (started). Test with an actual screen reader.
- **I2. Localization scaffolding.** Extract user-facing strings for i18n (the
  R41 copy work is a start); the sovereignty audience is global.

## Phase J — Voice & calls excellence (finish D1 + room voice)

- **J1.** Speaking/level indicators (Web Audio `AnalyserNode`), per-call quality
  stats (`getStats`: jitter/RTT/loss) in the participant panel, push-to-talk,
  input/output device pickers, a polished ring/answer/hangup flow.
- **J2.** Either ship the stubbed **room voice channels** (the signaling exists)
  with real UI, or remove the dead surface — don't leave a half-feature.

## Phase K — Mobile as a first-class client (D2)

Installable PWA polish (icons/splash, offline shell, push already wired in R47),
mobile-native interaction patterns (gestures, bottom-sheet, safe-area insets),
and a Tauri-mobile/Capacitor wrapper. A mobile-first layout pass on the web UI.

## Phase L — Durability (carried over from ROADMAP.md Phase E)

E1 identity backup/recovery UX, E2 multi-device (QR pairing), E3 Pod as the
optional "never lose anything" backbone, E4 reproducible/verifiable builds. Still
the long-term sovereignty promise; sequence after the polish phases unblock
adoption.

---

## Sequencing & rationale

```
F (design system)  ┐
                   ├─► the adoption unlock: a non-technical user must find it
G (usability)      ┘   beautiful and obvious in the first 60 seconds
        │
H (reliability)    ──► interleave throughout — trust is the product
        │
I (a11y)  J (voice)  K (mobile)  ──► breadth of "feels like a real app"
        │
L (durability)     ──► the long-term sovereignty promise
```

- **F + G are the highest ROI now.** The product is feature-complete but its
  first impression (visual coherence) and learnability (cognitive load) are the
  current ceiling on adoption — exactly the noobie thesis.
- **H interleaves.** Each polish change ships behind expanded automated coverage
  so we don't trade reliability for shine. The latent bugs found this session are
  the proof this matters.
- **F1/F2 also de-risk everything after** — a token system + de-inlined CSS makes
  a11y (I), mobile (K), and theming cheap.

## What to explicitly NOT do

- **No framework rewrite.** Vanilla + modules is working and tested; a design
  system is CSS + classes, not React.
- **No new top-level features before polishing the existing ones.** The gap is
  finish/feel, not breadth.
- **No teams/admin/enterprise surface, no SFU, no central directory** — same
  guardrails as ROADMAP.md; the target is 2–6 trusted friends.
- **Don't let Settings grow.** Every new toggle is a usability tax (Phase G is
  about shrinking it).

## Suggested next rounds

| Round | Theme | Phase |
|-------|-------|-------|
| R49 | Token-driven design system + de-inline CSS | F1–F2 |
| R50 | Empty/loading/error/offline state pass | F3 |
| R51 | Settings triage + onboarding finish (A3.2/A4.1) | G1–G2 |
| R52 | Cold-path bug sweep + AST free-name CI gate | H1 |
| R53 | User-journey E2E + two-gateway federation harness | H2 |
| R54 | WCAG 2.1 AA: focus, keyboard nav, contrast, SR | I1 |
| R55 | Voice UX: speaking/level/getStats/PTT; room-voice decision | J1–J2 |
| R56 | Mobile/PWA first-class | K |
| R57+ | Durability (E1–E4) | L |
