"""D5 transformation: wire AuthHandlerMixin into gateway.py."""
import re

with open(r'src\proxion_messenger_core\gateway.py', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"Input: {len(content.splitlines())} lines")

# Step 1 — import
content = content.replace(
    'from ._gateway_dm import DmHandlerMixin',
    'from ._gateway_dm import DmHandlerMixin\nfrom ._gateway_auth import AuthHandlerMixin',
)

# Step 2 — class definition
content = content.replace(
    'class ProxionGateway(VoiceHandlerMixin, PodSyncMixin, RoomHandlerMixin, DmHandlerMixin):',
    'class ProxionGateway(VoiceHandlerMixin, PodSyncMixin, RoomHandlerMixin, DmHandlerMixin, AuthHandlerMixin):',
)

# Step 3 — replace inline blocks
for cmd in ('auth_response', 'register'):
    method = '_handle_' + cmd
    pat = (
        r'            elif cmd == "' + re.escape(cmd) + r'":\n'
        r'((?!            elif |            else:).*\n)+'
    )
    repl = (
        f'            elif cmd == "{cmd}":\n'
        f'                await self.{method}(websocket, data)\n'
    )
    new = re.sub(pat, repl, content)
    if new == content:
        print(f"  WARNING: no match for {cmd}")
    else:
        print(f"  OK: {cmd}")
    content = new

print(f"Output: {len(content.splitlines())} lines")

with open(r'src\proxion_messenger_core\gateway.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done.")
