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

Generate a reusable identity with the CLI (prompts for a passphrase; defaults to
`~/.proxion/agent.json` if `--state` is omitted):

```
proxion agent init --state ./agent.json
```

## Running

```
pip install -e "./proxion-messenger-core[gateway,mcp]"
PROXION_GATEWAY_URL=ws://127.0.0.1:7474 \
PROXION_AGENT_STATE=./agent.json \
PROXION_AGENT_NAME="Research Agent" \
proxion-mcp
```

The server speaks MCP over stdio, so it drops into Claude Desktop or Claude Code
as a command-type server. Diagnostics go to stderr; stdout carries the MCP
protocol only.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROXION_GATEWAY_URL` | `ws://127.0.0.1:7474` | Gateway WebSocket URL |
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

## Status

This is a first cut focused on text messaging over the gateway. It reuses the
gateway command protocol verified by the end-to-end tests
(`tests/e2e/test_mcp_gateway_client.py`). Reactions, edits, presence, file
transfer, and a streaming "incoming messages" resource are the natural next
additions.
