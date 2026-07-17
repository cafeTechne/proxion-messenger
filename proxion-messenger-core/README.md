# proxion-messenger-core

The Python backend for [Proxion Messenger](../README.md): a library plus the
WebSocket/HTTP **gateway server** that powers the app. The gateway holds the
user's identity keys, persists messages locally, talks the Solid Protocol to
the user's pod, and federates directly with other gateways.

## Install

```bash
pip install -e .[gateway]        # gateway server (websockets)
pip install -e .[gateway,cli]    # + the `proxion` command-line tool
pip install -e .[gateway,cli,test]  # + test dependencies
```

Python ≥ 3.12.

## Module map

| Module | Responsibility |
|---|---|
| `gateway.py` | `ProxionGateway` — WebSocket routing, HTTP serving, room/DM/voice logic (composed from the `_gateway_*` mixins) |
| `persist.py` | `AgentState` — Ed25519 identity + X25519 store-key management |
| `local_store.py`, `_store/` | SQLite persistence: rooms, messages, relationships, devices, security state |
| `solid_client.py` | DPoP-authenticated Solid pod I/O (Community Solid Server oriented) |
| `messaging.py` | Signed message format, canonical serialization, hash chaining |
| `relay.py` | Cross-gateway federation: signed relay messages + full-payload envelope signatures |
| `certtoken.py` | Capability certificates: issuance, attenuation/caveats, revocation, proof-of-possession |
| `cli.py` | The `proxion` CLI (identity, certs, pod, doctor, …) |

See [CAPABILITIES.md](CAPABILITIES.md) for the security-primitive details and
[docs/PROTOCOL.md](docs/PROTOCOL.md) for the wire protocol.

## Running the gateway

Usually via the repo root (`python run_gateway.py`) or the desktop app, which
bundles this package as a PyInstaller sidecar. For development instances see
`../scripts/run_test_gateway.py` and `../scripts/run_local_pair.py`.

## Tests

```bash
pytest                        # unit tests
pytest tests/e2e/             # end-to-end (real WebSocket connections)
pytest -m "not integration"   # skip tests that need a running CSS pod
```

## License

[AGPL-3.0](LICENSE), same as the rest of the repository.
