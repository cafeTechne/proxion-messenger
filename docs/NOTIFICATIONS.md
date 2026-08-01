# Notifications

Proxion delivers chat activity in three ways, with different reach.

## In-app, real time

While Proxion is open and signed in to a pod, new messages and chat invitations arrive
live over the Solid Notifications channel (WebSocket, with polling as a fallback). This
works over the open internet against any reachable pod. Nothing beyond a pod connection
is required.

## Closed-app push

To notify you when no Proxion tab is open, something always-on has to send a Web Push.
The pod cannot do this itself: the Community Solid Server implements the WebSocket,
Webhook, and Streaming HTTP notification channels, but not a Web Push channel, so it
cannot post directly to a browser's push service.

Proxion's gateway fills that role. Your pod's server is told to POST the gateway when
your inbox changes (the standard Webhook channel), and the gateway relays a
content-free Web Push to your devices. The push carries no sender and no message text;
opening Proxion reads the actual invitation, which is still access-checked.

This needs the gateway to be reachable by your pod's server, the same way your pod needs
to be reachable to sync at all. It is the requirement Proxion federation already has: a
gateway that peers can reach. Decentralized does not mean zero-infrastructure. You run
your own gateway, reachable, the same way you run your own pod. There is no central
Proxion service in the path.

The settings panel shows the honest status:

- **Background notifications: on** — the app is served from a public origin, so the
  gateway is reachable and closed-app push is expected to work.
- **Notifications only while Proxion is open** — the app is served from a loopback or
  private address (for example the Tauri desktop app's local sidecar, or a LAN-only
  gateway). A remote pod cannot reach that gateway, so only the in-app path works.
- **Background notifications: off** — notification permission has not been granted.

## Behind NAT, without opening a port

If you run an always-on gateway at home but do not want to expose an inbound port, the
gateway can instead subscribe to your inbox over an outbound WebSocket and send the push
itself when the inbox changes. This needs no inbound reachability. It asks you to grant
the gateway's WebID read access to your inbox, a read-only, inbox-scoped delegation.

## Persisting the push identity

The gateway signs pushes with a VAPID key. Set `PROXION_VAPID_PRIVATE_KEY` (and the
matching public key and subject) so the key is stable across restarts. Without it the
gateway generates an ephemeral key on each start, and devices re-register on next login.
