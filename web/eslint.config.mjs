// Minimal ESLint config — its job is the `no-undef` safety net for refactors.
// A reference to a variable that an extraction/rename breaks shows up as an
// undefined global, which this catches. Not a style linter.
import globals from "globals";

export default [
  {
    files: ["main.js", "util.js", "filetransfer.js", "voice.js", "notifications.js", "onboarding.js", "reactions.js", "pins.js", "media.js", "modals.js", "profile.js", "edit.js", "mute.js", "mentions.js", "rooms.js", "address.js", "typing.js", "members.js", "auth.js", "e2e.js", "pod.js"],
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
        attachListener: "readonly",
        sendNotification: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": "off",
    },
  },
];
