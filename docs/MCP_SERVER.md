# Proxion MCP server

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent
participate in Proxion as a first-class, cryptographically-signed identity. The
agent can list and create rooms, read history, and send room messages or direct
messages. Every action travels through a Proxion gateway over the same
WebSocket protocol the web client uses, so the agent is bound by the same
consent, contact, and membership rules as any human participant.

## Why the gateway path

The server is a WebSocket client of a running gateway (`gateway_client.py`),
not a separate in-process client. That choice means the agent joins the exact
real-time fabric human users are on, inherits federation, and cannot bypass the
authorization checks the gateway already enforces. A direct message to someone
who is not a contact triggers the normal friend-request flow rather than silent
delivery.

## Identity

The agent registers a `did:key` derived from an Ed25519 identity key.

- Set `PROXION_AGENT_STATE` to the path of an `agent.json` (and
  `PROXION_AGENT_PASSPHRASE` if it is encrypted) to run as a persistent
  identity across sessions.
- Leave it unset to generate a fresh ephemeral identity for the session. This
  is convenient for testing and produces a new DID each run.

Generate a reusable identity with the CLI (needs the `cli` extra; prompts for a
passphrase; defaults to `~/.proxion/agent.json` if `--state` is omitted):

```
pip install -e "./proxion-messenger-core[cli]"
proxion agent init --state ./agent.json
```

### Messages to and from an agent are not end-to-end encrypted

DM encryption runs in the browser (`web/e2e.js`). The agent registers without an
X25519 key, so a person messaging it must choose "Send unencrypted" for that
contact, and the agent's own messages travel as plaintext through the gateways.
Treat an agent conversation like a room, not like a private DM.

## Running

```
pip install -e "./proxion-messenger-core[gateway,mcp]"
PROXION_GATEWAY_URL=ws://127.0.0.1:7474 \
PROXION_AGENT_STATE=./agent.json \
PROXION_AGENT_NAME="Research Agent" \
proxion-mcp
```

Which URL to use:

- **Desktop app running:** its gateway listens on plain `ws://127.0.0.1:7474`, the default.
- **`python run_gateway.py`:** it serves TLS with a self-signed cert. Use
  `wss://127.0.0.1:7474` and set `PROXION_GATEWAY_CAFILE` to the gateway's
  `<data dir>/tls/cert.pem`, or set `PROXION_DATA_DIR` to the same data dir and the
  cert is found automatically. Verification stays on; the cert is trusted, not skipped.

The server speaks MCP over stdio, so it drops into Claude Desktop or Claude Code
as a command-type server. Diagnostics go to stderr; stdout carries the MCP
protocol only.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROXION_GATEWAY_URL` | `ws://127.0.0.1:7474` | Gateway WebSocket URL |
| `PROXION_GATEWAY_CAFILE` | `<PROXION_DATA_DIR>/tls/cert.pem` if present | Extra cert to trust for a `wss://` gateway |
| `PROXION_AGENT_STATE` | (unset) | Path to a persistent `agent.json` |
| `PROXION_AGENT_PASSPHRASE` | (unset) | Passphrase for an encrypted state file |
| `PROXION_AGENT_NAME` | `Proxion Agent` | Display name announced on register |

## Tools

| Tool | Purpose |
| --- | --- |
| `whoami` | The agent's `did:key`, display name, and WebID |
| `list_rooms` | Rooms the agent is a member of |
| `list_direct_messages` | The agent's DM threads |
| `create_room(name)` | Create a room, returns its id and join code |
| `join_room(code)` | Join a room by invite/join code |
| `send_room_message(room_id, text)` | Post to a room |
| `send_direct_message(contact_did, text)` | DM a contact by `did:key` |
| `read_history(thread_id, limit)` | Recent messages from a room or DM thread |
| `search_messages(query)` | Search the agent's accessible messages |
| `edit_message(thread_id, message_id, text)` | Edit a message the agent sent |
| `delete_message(thread_id, message_id)` | Delete a message the agent sent |
| `react_to_message(thread_id, message_id, emoji)` | React to a message |
| `get_room_members(room_id)` | Members of a room the agent belongs to |

## Status

The tools cover text messaging, edits, deletes and reactions over the gateway,
using the command protocol exercised by `tests/e2e/test_mcp_gateway_client.py`
(plain `ws://`) and `tests/test_desktop_sidecar.py`
(`test_mcp_client_connects_to_a_direct_run_gateway`, `wss://` against
`run_gateway.py`). Presence, file transfer and a streaming "incoming messages"
resource are not implemented.
