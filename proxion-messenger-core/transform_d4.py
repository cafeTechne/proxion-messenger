"""D4 transformation: wire DmHandlerMixin into gateway.py."""
import re

with open(r'src\proxion_messenger_core\gateway.py', 'r', encoding='utf-8') as f:
    content = f.read()

orig_lines = len(content.splitlines())
print(f"Input: {orig_lines} lines")

# Step 1 — import
content = content.replace(
    'from ._gateway_rooms import RoomHandlerMixin',
    'from ._gateway_rooms import RoomHandlerMixin\nfrom ._gateway_dm import DmHandlerMixin',
)

# Step 2 — class definition
content = content.replace(
    'class ProxionGateway(VoiceHandlerMixin, PodSyncMixin, RoomHandlerMixin):',
    'class ProxionGateway(VoiceHandlerMixin, PodSyncMixin, RoomHandlerMixin, DmHandlerMixin):',
)

# Step 3a — replace the leading `if cmd == "send_dm":` block (not elif)
# Match: "        if cmd == \"send_dm\":\n" + body lines until next "            elif"
pat_send_dm = (
    r'        if cmd == "send_dm":\n'
    r'((?!            elif |            else:).*\n)+'
)
repl_send_dm = (
    '        if cmd == "send_dm":\n'
    '                await self._handle_send_dm(websocket, data)\n'
)
new = re.sub(pat_send_dm, repl_send_dm, content)
if new == content:
    print("  WARNING: no match for send_dm")
else:
    print("  OK: send_dm (if block)")
content = new

# Step 3b — replace elif-based DM handlers
dm_commands = [
    'edit_message',
    'get_dms',
    'send_file',
    'link_pod',
    'local_dm',
]

for cmd in dm_commands:
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

out_lines = len(content.splitlines())
print(f"Output: {out_lines} lines  (removed {orig_lines - out_lines})")

with open(r'src\proxion_messenger_core\gateway.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done.")
