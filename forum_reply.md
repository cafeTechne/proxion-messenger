Thanks for pointing me to that thread. It's a genuinely good discussion, and it made me realize my README was giving the wrong impression on both counts. Let me try to clarify, because I think Proxion actually lands on the side you and Noel were arguing for.

## On E2E vs interoperability

I think the confusion is that "E2E" in Proxion means something different from what Vault does. Vault encrypts data at rest on the pod, which is the thing that breaks interop: another app opens the file and sees ciphertext. Proxion doesn't do that.

Room history gets written to your pod as plain, typed JSON-LD, using a documented vocabulary (a little `px:` namespace: `px:content`, `px:fromWebid`, `px:timestamp`, all in the clear). Any Solid app you authorize can read it. It's normal Solid resources, not a lock-box.

The end-to-end encryption in Proxion is a *transport* thing: Double Ratchet on the wire between you and the person you're talking to, so the relays and other people's gateways that shuttle the message around can't read it in flight. That's a totally different axis from "what format is my data in when it's sitting on my own pod." Your own pod holds readable data; the encryption just stops the middlemen from reading messages in transit. So I don't think it defeats the interop purpose. The pod is still the open, portable, multi-app substrate.

To put my money where my mouth is, I just wrote up the full storage contract so it's not just a claim: [the pod data model doc](https://github.com/cafeTechne/proxion-messenger/blob/main/docs/POD_DATA_MODEL.md). It documents every resource type, the vocabulary, the access-control model, and a short example of reading Proxion's data from another Solid app with no Proxion code involved.

I'll be honest about where it doesn't fully hold up yet, though: 1:1 DM history currently lives in local device storage rather than as RDF on your pod, so a DM archive isn't pod-interoperable the way rooms are. That's on my roadmap (an opt-in plaintext-on-your-own-pod DM archive, which is safe since it's your pod and you can already read your own messages), not a settled ideal. It's just where the current build is.

## On the gateway / Python server

Yeah, my README buried the lede here, and I've just fixed it. Regular users never run a server or touch Python. The desktop app is a normal installer, and the gateway is bundled *inside* it as a sidecar that starts with the app. You just double-click and go. The `python run_gateway.py` bit that you probably saw is the from-source / self-host path, which I've now clearly labeled as the advanced route.

Why there's a gateway at all: real-time federated messaging needs a transport component that a pure browser-to-pod app can't provide on its own. Persistent presence, WebRTC signaling, NAT traversal, store-and-forward relay, gateway-to-gateway federation. It's the same reason Matrix has homeservers and email has SMTP servers. Solid is brilliant for the data and identity layer (WebID, your pod), but it was never meant to be a real-time delivery protocol, so I use it for what it's great at and add a thin transport layer for the rest.

The one honest limitation there is mobile. A phone can't bundle the gateway, so the PWA is a thin client that connects to a gateway running somewhere else (your desktop, or one you host). That's a real dependency I'm not going to pretend away.

Anyway, appreciate the sharp feedback. It directly improved both the docs and how I explain the project. Curious whether the transport-vs-at-rest split changes how you see the interop question.
