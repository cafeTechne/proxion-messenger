# Screen reader walkthrough (NVDA / VoiceOver)

Automated checks (axe-core WCAG 2.2 AA, a mouse-free keyboard journey, a pseudo-locale
and RTL pass, and contrast) run on every push. They cannot judge whether the app is
pleasant to actually listen to. This is the manual pass to run and record before a
release, focused on the surfaces automation reaches least: calls and cross-app interop.

Run with NVDA on Windows (Firefox or Chrome) and, if available, VoiceOver on macOS
(Safari). Record the date, the tool and version, and any issue found.

## Programmatic verification (2026-08)

Beyond axe, an accessibility-tree audit (Chrome's a11y snapshot, what a screen reader
consumes) was run on the reachable surfaces. Results, so the manual pass can skip them:

- **Main shell**, **Settings dialog**, and the **New Solid conversation dialog** expose
  zero unnamed interactive controls and correct headings (Proxion / ROOMS / DMS / SOLID
  CHATS / Welcome / Settings / New Solid conversation). So checklist item 7's fields are
  confirmed named at the tree level; the manual pass only needs to confirm they read well in
  order.
- axe (WCAG 2.2 AA) is clean on welcome, room-with-messages, settings, members-panel,
  emoji-picker, shortcut-modal, and onboarding.

What the tree audit could NOT reach headless: the **call surfaces** (they render only in a
live call) and, by nature, whether the app is *pleasant to listen to* (reading order, and
whether the `aria-live` regions actually announce audibly on change). Those are the focus of
the manual NVDA run below.

## What is already wired for screen readers

- Call state, the on-air capture indicator, the connection-quality indicator, and the
  verified/unverified badge are `role="status"` with `aria-live="polite"`, so changes are
  announced without stealing focus.
- The call and preview controls have text labels or `aria-label`s and are keyboard
  reachable; Escape closes the preview and exits fullscreen.
- Opening the pre-join preview moves focus to Join; closing it returns focus to the
  control that opened it.

## Checklist

### Calls

1. From a direct message, reach the Call and Video call buttons by keyboard alone and
   confirm each is announced with a clear name.
2. Start a video call. Confirm the pre-join preview is announced as a dialog, the camera
   and microphone pickers are labelled, focus lands on Join, and Escape cancels and
   returns focus to the Video call button.
3. In a connected call, confirm the mute, camera, screen share, quality, fullscreen, and
   end controls are all reachable and named. Change the quality selector and confirm the
   new value is announced.
4. Confirm the connection-quality change (good/fair/poor) and the verified/unverified
   badge are announced when they change, without moving focus.
5. Toggle fullscreen; confirm Escape exits it and focus is not lost.

### Cross-app chat

6. Open Solid Chats. Confirm the empty state text is read and explains discovery and
   invitations.
7. In the New Solid conversation dialog, confirm every field (host name, participant,
   join link, discover WebID) and the invitations list are labelled and reachable.
8. Receive an invitation and confirm the Accept and Dismiss buttons are named.

### Notifications

9. Confirm the background-notification status line in settings is read and its meaning is
   clear (on / in-app only / off).

Record results below each release; file any issue as a scoped bug.
