# Proxion Web UI Scaffold

This is a minimal static web interface for the Proxion federated messaging platform. It connects to the Proxion WebSocket Gateway to provide real-time messaging, presence, and room support.

## Prerequisites

1.  **Proxion Gateway**: You must have the Proxion gateway running locally.
    ```bash
    proxion chat gateway --port 7474
    ```
2.  **Environment**: Ensure `CSS_ALICE_URL`, `CSS_CLIENT_ID`, and `CSS_CLIENT_SECRET` are set in your environment so the gateway can connect to your Pod.

## How to Use

1.  Open `index.html` in any modern web browser.
2.  If the gateway is running on the default port (7474), the UI will connect automatically.
3.  New direct messages and room activities will appear in the sidebar. Click on a thread or room to start chatting.

## Features Demonstrated

- **Real-time Messaging**: Incoming messages from any federated peer are broadcasted via the gateway and rendered in the feed.
- **Unified Inbox**: Both DMs and Rooms are handled in a single connection.
- **E2E Decryption**: The gateway handles decryption, so the Web UI receives plaintext content.
- **Presence**: Connection status and peer presence dots are updated dynamically.
- **Auto-reconnect**: The UI will attempt to reconnect to the gateway every 3 seconds if the connection is lost.

## Extending the UI

The UI is built with vanilla JavaScript and CSS for simplicity. You can extend it by adding support for:
- Avatar rendering (using `identity.get_avatar`).
- File attachment previews.
- Voice channel participant lists.
- Thread-specific message history via gateway commands.
