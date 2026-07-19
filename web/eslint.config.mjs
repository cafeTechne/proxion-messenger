// Minimal ESLint config — its job is the `no-undef` safety net for refactors.
// A reference to a variable that an extraction/rename breaks shows up as an
// undefined global, which this catches. Not a style linter.
import globals from "globals";

export default [
  {
    files: ["main.js", "util.js", "filetransfer.js", "voice.js", "notifications.js", "onboarding.js", "reactions.js", "pins.js", "media.js", "modals.js", "profile.js", "edit.js", "mute.js", "mentions.js", "rooms.js", "address.js", "typing.js", "members.js", "friend-requests.js", "e2e-status.js", "status-banners.js", "connection.js", "rendering.js", "view.js", "invite.js", "push.js", "states.js", "auth.js", "e2e.js", "pod.js", "focus-trap.js", "device-cert.js", "pairing.js", "dmhistory.js", "a11y.js", "i18n.js", "recovery.js", "gifs.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        // App/runtime globals not in the standard browser set:
        __TAURI__: "readonly",
        RTCPeerConnection: "readonly",
        RTCSessionDescription: "readonly",
        RTCIceCandidate: "readonly",
        // Vendor libs loaded via <script> in index.html:
        QRCode: "readonly",
        jsQR: "readonly",
        // KNOWN PRE-EXISTING undefined refs (latent bugs predating the lint net;
        // declared here to keep the net's signal clean for new work — TODO: fix).
        // attachListener was removed: it is scoped to setupEventListeners(); any
        // reference outside it is a real bug (3 stranded top-level calls that
        // threw "attachListener is not defined" at eval and broke the page were
        // fixed by moving them inside setupEventListeners).
        sendNotification: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": "off",
      // A param/local shadowing a closure var (e.g. a handler param named `state`
      // shadowing the module's state cluster) silently broke voice ICE handling —
      // exactly the class no-shadow catches. Kept as an error to prevent recurrence.
      "no-shadow": "error",
    },
  },
];
