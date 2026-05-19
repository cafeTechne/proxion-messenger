"""Example demonstrating Proxion Core social features (Reactions & Threads)."""
from __future__ import annotations

import asyncio
from proxion_messenger_core import AgentState
from proxion_messenger_core.messaging import compose
from proxion_messenger_core.reactions import add_reaction
from proxion_messenger_core.replies import build_thread_view

async def demonstrate_social_features():
    # 1. Setup local identity
    alice = AgentState.generate()
    print(f"Alice's WebID: {alice.identity_pub_bytes.hex()}")

    # 2. In a real application, you would perform a handshake to get a 
    # RelationshipCertificate. For this demonstration, we use a placeholder.
    mock_cert = None 
    
    # 3. Composing a root message
    m1 = compose(alice.identity_key, mock_cert, "Welcome to the decentralized web!")
    print(f"\n[Root] Alice says: {m1.content}")
    print(f"Message ID: {m1.message_id}")

    # 4. Composing a threaded reply
    m2 = compose(alice.identity_key, mock_cert, "It's much better here.", reply_to_id=m1.message_id)
    print(f"\n[Reply] Alice replies: {m2.content}")
    print(f"Reply to: {m2.reply_to_id}")

    # 5. Adding a reaction
    # Reactions are specialized messages with type='reaction'
    r1 = add_reaction(alice.identity_key, mock_cert, m1.message_id, "🚀")
    print(f"\n[Reaction] Alice added 🚀 to {m1.message_id}")

    # 6. Visualizing the thread
    # build_thread_view aggregates replies under their roots and sorts them by time
    msgs = [m1, m2, r1]
    thread = build_thread_view(msgs)
    
    for root, replies in thread:
        print(f"\n--- Thread ---")
        print(f"ROOT: {root.content}")
        for r in replies:
            print(f"  REPLY: {r.content}")

if __name__ == "__main__":
    asyncio.run(demonstrate_social_features())
