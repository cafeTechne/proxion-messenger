"""Proxion CLI — manage an agent identity and coordinate with peers.

Commands
--------
``proxion agent init``
    Generate a new agent (Ed25519 identity + X25519 store key) and save it to
    a state file encrypted with a passphrase.

``proxion agent info``
    Print the agent's public keys and list known certificates.

``proxion store serve``
    Run the coordination store HTTP server.  Backed by SQLite so messages
    survive restarts.

``proxion agent invite``
    Send a federation invite to a peer's coordination store URL.
    Prints the invitation ID so you can track the handshake.

``proxion agent accept``
    Poll your mailbox for pending invites, display them, and interactively
    accept one.  Completes the handshake and saves the resulting certificate.

``proxion agent status``
    Show mailbox stats (pending messages, bytes) and certificate summary.

State file
----------
By default the state file is ``~/.proxion/agent.json``.  Override with
``--state`` (all commands) or the ``PROXION_STATE`` environment variable.

Passphrase
----------
All commands that load the state file will prompt for the passphrase
interactively.  Set ``PROXION_PASSPHRASE`` in the environment to skip the
prompt (useful for scripts, not recommended for interactive use).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Lazy imports — keep startup fast; only import heavy libs when needed
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="proxion",
    help="Proxion agent management and coordination store.",
    no_args_is_help=True,
)
agent_app = typer.Typer(help="Agent identity commands.", no_args_is_help=True)
store_app = typer.Typer(help="Coordination store commands.", no_args_is_help=True)
validator_app = typer.Typer(help="Validator server commands.", no_args_is_help=True)
mailbox_app = typer.Typer(help="Mailbox inspection commands.", no_args_is_help=True)
cert_app = typer.Typer(help="Certificate management commands.", no_args_is_help=True)
ledger_app = typer.Typer(help="Token ledger commands.", no_args_is_help=True)
chat_app = typer.Typer(help="Proxion messaging commands.", no_args_is_help=True)
chat_dm_app = typer.Typer(help="Direct message commands.", no_args_is_help=True)
chat_room_app = typer.Typer(help="Room commands.", no_args_is_help=True)
chat_file_app = typer.Typer(help="File sharing commands.", no_args_is_help=True)
chat_presence_app = typer.Typer(help="Presence commands.", no_args_is_help=True)
chat_identity_app = typer.Typer(help="Identity profile commands.", no_args_is_help=True)
chat_export_app = typer.Typer(help="Export chat history.", no_args_is_help=True)
device_app = typer.Typer(help="Multi-device account management.", no_args_is_help=True)
did_app = typer.Typer(help="DID utilities.", no_args_is_help=True)

app.add_typer(agent_app, name="agent")
app.add_typer(store_app, name="store")
app.add_typer(validator_app, name="validator")
app.add_typer(cert_app, name="cert")
app.add_typer(ledger_app, name="ledger")
app.add_typer(chat_app, name="chat")
app.add_typer(device_app, name="device")
app.add_typer(did_app, name="did")
store_app.add_typer(mailbox_app, name="mailbox")
chat_app.add_typer(chat_dm_app, name="dm")
chat_app.add_typer(chat_room_app, name="room")
chat_app.add_typer(chat_file_app, name="file")
chat_app.add_typer(chat_presence_app, name="presence")
chat_app.add_typer(chat_identity_app, name="identity")
chat_app.add_typer(chat_export_app, name="export")


console = Console()

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DEFAULT_STATE = Path.home() / ".proxion" / "agent.json"
_DEFAULT_DB = Path.home() / ".proxion" / "store.db"

_STATE_OPTION = typer.Option(
    None, "--state", "-s",
    help="Path to agent state file (default: ~/.proxion/agent.json).",
    envvar="PROXION_STATE",
)
_PASSPHRASE_OPTION = typer.Option(
    None, "--passphrase",
    help="Passphrase for the state file (prompted if omitted).",
    envvar="PROXION_PASSPHRASE",
)


def _resolve_state(state: Optional[str]) -> Path:
    return Path(state) if state else _DEFAULT_STATE


def _get_passphrase(passphrase: Optional[str], prompt: str = "Passphrase") -> bytes:
    if passphrase:
        return passphrase.encode("utf-8")
    p = typer.prompt(prompt, hide_input=True)
    return p.encode("utf-8")


def _load_state(state_path: Path, passphrase: bytes):
    from .persist import AgentState, PersistError
    try:
        return AgentState.load(state_path, passphrase)
    except PersistError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


class SimpleStash:
    """A simple key-value store for storing data in a directory."""
    
    def __init__(self, stash_dir: Path):
        self.dir = stash_dir
        self.dir.mkdir(parents=True, exist_ok=True)
    
    def get_sync(self, key: str):
        """Get a value by key synchronously. Returns None if not found."""
        path = self.dir / key
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
    
    async def get(self, key: str):
        """Get a value by key. Returns None if not found."""
        return self.get_sync(key)
    
    async def put(self, key: str, value: bytes):
        """Store a value by key."""
        path = self.dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    
    async def delete(self, key: str):
        """Delete a key. Does nothing if not found."""
        path = self.dir / key
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    
    async def list(self, prefix: str = ""):
        """List all keys with optional prefix."""
        result = []
        start_dir = self.dir / prefix if prefix else self.dir
        if not start_dir.exists():
            return result
        for path in start_dir.rglob("*"):
            if path.is_file():
                rel_path = path.relative_to(self.dir)
                result.append(str(rel_path).replace("\\", "/"))
        return result


def _load_stash(state_path: Path) -> SimpleStash:
    """Load or create a stash directory next to the state file."""
    stash_dir = state_path.parent / ".stash"
    return SimpleStash(stash_dir)


# ---------------------------------------------------------------------------
# agent init
# ---------------------------------------------------------------------------

@agent_app.command("init")
def agent_init(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing state file."),
):
    """Generate a new agent identity and save it to the state file."""
    from .persist import AgentState

    state_path = _resolve_state(state)
    if state_path.exists() and not force:
        console.print(
            f"[yellow]State file already exists:[/yellow] {state_path}\n"
            "Use --force to overwrite."
        )
        raise typer.Exit(1)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    pw = _get_passphrase(passphrase, "New passphrase")
    confirm = _get_passphrase(None, "Confirm passphrase")
    if pw != confirm:
        console.print("[red]Passphrases do not match.[/red]")
        raise typer.Exit(1)

    agent = AgentState.generate()
    agent.save(state_path, pw)

    console.print(f"[green]Agent created.[/green] State saved to: {state_path}")
    console.print(f"  Identity pubkey : [cyan]{agent.identity_pub_bytes.hex()}[/cyan]")
    console.print(f"  Store pubkey    : [cyan]{agent.store_pub_bytes.hex()}[/cyan]")


# ---------------------------------------------------------------------------
# agent info
# ---------------------------------------------------------------------------

@agent_app.command("info")
def agent_info(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Print the agent's public keys and known certificates."""
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    console.print(f"\n[bold]Agent state:[/bold] {state_path}")
    console.print(f"  Identity pubkey : [cyan]{agent.identity_pub_bytes.hex()}[/cyan]")
    console.print(f"  Store pubkey    : [cyan]{agent.store_pub_bytes.hex()}[/cyan]")

    if not agent.certificates:
        console.print("\n  No certificates.")
        return

    table = Table(title="Certificates", show_lines=True)
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Issuer", max_width=16)
    table.add_column("Subject", max_width=16)
    table.add_column("Capabilities")
    table.add_column("Expires")

    import datetime as _dt
    for cert in agent.certificates:
        caps = ", ".join(f"{c.can}:{c.with_}" for c in cert.capabilities)
        exp = _dt.datetime.fromtimestamp(cert.expires_at).strftime("%Y-%m-%d")
        table.add_row(
            cert.certificate_id[:8] + "…",
            cert.issuer[:14] + "…" if len(cert.issuer) > 14 else cert.issuer,
            cert.subject[:14] + "…" if len(cert.subject) > 14 else cert.subject,
            caps or "(none)",
            exp,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# agent list-certs
# ---------------------------------------------------------------------------

@agent_app.command("list-certs")
def agent_list_certs(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """List all stored certificates with revocation status."""
    from .revocation import certificate_revocation_id
    import datetime as _dt

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    if not agent.certificates:
        typer.echo("No certificates stored.")
        return

    table = Table(title="Certificates", show_lines=True)
    table.add_column("Cert ID", style="dim", max_width=12)
    table.add_column("Issuer", max_width=16)
    table.add_column("Subject", max_width=16)
    table.add_column("Capabilities")
    table.add_column("Expires")
    table.add_column("Revoked")

    now_dt = _dt.datetime.now(_dt.timezone.utc)
    for cert in agent.certificates:
        rev_id = certificate_revocation_id(cert)
        is_revoked = agent.revocation_list.is_revoked(rev_id, now_dt)
        caps = ", ".join(f"{c.can}" for c in cert.capabilities) or "(none)"
        exp = _dt.datetime.fromtimestamp(cert.expires_at, tz=_dt.timezone.utc).strftime("%Y-%m-%d")
        revoked_str = "[red]YES[/red]" if is_revoked else "[green]no[/green]"
        table.add_row(
            cert.certificate_id[:8] + "…",
            cert.issuer[:14] + "…" if len(cert.issuer) > 14 else cert.issuer,
            cert.subject[:14] + "…" if len(cert.subject) > 14 else cert.subject,
            caps,
            exp,
            revoked_str,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# agent invite
# ---------------------------------------------------------------------------

@agent_app.command("invite")
def agent_invite(
    peer_store_url: str = typer.Argument(help="HTTP URL of the peer's coordination store."),
    capability: list[str] = typer.Option(
        [], "--cap", "-c",
        help="Capability to offer, format: 'action:resource' e.g. 'read:stash://me/shared/'.",
    ),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Send a federation invite to a peer's coordination store."""
    from .persist import AgentState
    from .federation import Capability
    from .handshake import create_invite, send_invite
    from .store_client import RemoteStore
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    caps = []
    for cap_str in capability:
        try:
            action, resource = cap_str.split(":", 1)
        except ValueError:
            console.print(f"[red]Invalid capability format:[/red] {cap_str!r} — use 'action:resource'")
            raise typer.Exit(1)
        caps.append(Capability(with_=resource, can=action))

    if not caps:
        console.print("[yellow]Warning:[/yellow] Sending invite with no capabilities.")

    store_pub_bytes = agent.store_pub_bytes
    invite = create_invite(agent.identity_key, store_pub_bytes, caps)

    # The peer store URL is where we post — we need the peer's store pubkey
    # to address their mailbox.  Without a discovery protocol yet, we ask the
    # user to supply it via --peer-store-key, or we post to a well-known
    # endpoint that returns the server's store pubkey.
    console.print(f"\n[bold]Sending invite to:[/bold] {peer_store_url}")
    console.print(f"  Invitation ID: [cyan]{invite.invitation_id}[/cyan]")
    console.print(f"  Your store pubkey: [cyan]{store_pub_bytes.hex()}[/cyan]")

    # Auto-discover the peer's store pubkey from GET /info
    import httpx as _httpx
    peer_key_hex: Optional[str] = None
    try:
        info_resp = _httpx.get(f"{peer_store_url.rstrip('/')}/info", timeout=5.0)
        info_resp.raise_for_status()
        peer_key_hex = info_resp.json().get("store_pubkey")
        if peer_key_hex:
            console.print(f"  Peer store pubkey: [cyan]{peer_key_hex[:16]}…[/cyan] (discovered via /info)")
    except Exception as exc:
        console.print(f"[yellow]Could not auto-discover peer pubkey:[/yellow] {exc}")

    if not peer_key_hex:
        peer_key_hex = typer.prompt("Peer's store pubkey (hex 32 bytes)")

    try:
        peer_store_pub = bytes.fromhex(peer_key_hex.strip())
        if len(peer_store_pub) != 32:
            raise ValueError("must be 32 bytes")
    except ValueError as exc:
        console.print(f"[red]Invalid peer store pubkey:[/red] {exc}")
        raise typer.Exit(1)

    remote = RemoteStore(peer_store_url)
    send_invite(invite, peer_store_pub, remote)
    remote.close()

    # Persist the invite so `agent finalize` can complete the handshake later
    from .persist import PendingInvite
    agent.pending_invites.append(
        PendingInvite(invite=invite, peer_store_pub_hex=peer_key_hex.strip())
    )
    pw2 = _get_passphrase(passphrase, "Passphrase to save state")
    agent.save(state_path, pw2)

    console.print(f"[green]Invite sent.[/green] Invitation ID: [cyan]{invite.invitation_id}[/cyan]")
    console.print("Run [bold]proxion agent finalize[/bold] once the peer has accepted.")


# ---------------------------------------------------------------------------
# agent accept
# ---------------------------------------------------------------------------

@agent_app.command("accept")
def agent_accept(
    store_url: str = typer.Argument(help="URL of the coordination store where your mailbox lives."),
    capability: list[str] = typer.Option(
        [], "--cap", "-c",
        help="Capability to offer in return, format: 'action:resource'.",
    ),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Poll your mailbox, display pending invites, and accept one interactively."""
    from .persist import AgentState
    from .federation import Capability
    from .handshake import receive_invites, accept_invite, receive_acceptances, finalize_handshake, send_certificate
    from .store_client import RemoteStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    caps = []
    for cap_str in capability:
        try:
            action, resource = cap_str.split(":", 1)
        except ValueError:
            console.print(f"[red]Invalid capability format:[/red] {cap_str!r}")
            raise typer.Exit(1)
        caps.append(Capability(with_=resource, can=action))

    remote = RemoteStore(store_url)

    # Receive pending invites from our mailbox
    invites_with_validity = receive_invites(agent.store_key, remote)
    if not invites_with_validity:
        console.print("No pending invites in mailbox.")
        remote.close()
        return

    console.print(f"\nFound [cyan]{len(invites_with_validity)}[/cyan] pending invite(s):\n")
    for i, (inv, valid) in enumerate(invites_with_validity):
        status = "[green]valid[/green]" if valid else "[red]invalid signature[/red]"
        issuer_pub = inv.issuer.get("public_key", "?")[:16] + "…"
        console.print(f"  [{i}] ID={inv.invitation_id[:16]}… issuer={issuer_pub} sig={status}")
        for cap in inv.capabilities:
            console.print(f"       cap: {cap.can}:{cap.with_}")

    choice = typer.prompt("\nAccept which invite? (index, or 'n' to cancel)", default="n")
    if choice.lower() == "n":
        console.print("Cancelled.")
        remote.close()
        return

    try:
        idx = int(choice)
        chosen_invite, valid = invites_with_validity[idx]
    except (ValueError, IndexError):
        console.print("[red]Invalid choice.[/red]")
        remote.close()
        raise typer.Exit(1)

    if not valid:
        console.print("[red]Cannot accept invite with invalid signature.[/red]")
        remote.close()
        raise typer.Exit(1)

    accept_invite(chosen_invite, agent.identity_key, agent.store_pub_bytes, caps, remote)

    # Now wait for Alice to finalise and send the certificate back
    console.print("Acceptance sent. Waiting for certificate from issuer…")
    console.print("(Run 'proxion agent status' to check later, or wait here)")

    # Poll for the certificate — issuer must run 'proxion agent finalize'
    # For now, just save and exit
    console.print("[yellow]Handshake acceptance sent.[/yellow]")
    console.print("The issuer must call 'proxion agent finalize' to complete the handshake.")
    remote.close()


# ---------------------------------------------------------------------------
# agent receive-certs
# ---------------------------------------------------------------------------

@agent_app.command("receive-certs")
def agent_receive_certs(
    store_url: str = typer.Argument(help="URL of the coordination store where your mailbox lives."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Drain incoming certificates from your mailbox and save them to state.

    Run this after the invite issuer has called 'proxion agent finalize'.
    Each certificate whose signature is valid is added to the agent's certificate
    store; invalid-signature certs are logged and discarded.
    """
    from .handshake import receive_certificates
    from .store_client import RemoteStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    remote = RemoteStore(store_url)
    try:
        cert_pairs = receive_certificates(agent.store_key, remote)
    except Exception as exc:
        console.print(f"[red]Failed to receive certificates:[/red] {exc}")
        remote.close()
        raise typer.Exit(1)
    finally:
        remote.close()

    if not cert_pairs:
        console.print("No incoming certificates in mailbox.")
        return

    console.print(f"Found [cyan]{len(cert_pairs)}[/cyan] certificate(s).")
    new_certs = []
    for cert, valid in cert_pairs:
        if not valid:
            console.print(f"  [yellow]Discarding[/yellow] cert {cert.certificate_id[:8]}… — invalid signature.")
            continue
        # Skip duplicates already in state
        existing_ids = {c.certificate_id for c in agent.certificates}
        if cert.certificate_id in existing_ids:
            console.print(f"  [dim]Skipping[/dim] cert {cert.certificate_id[:8]}… — already stored.")
            continue
        new_certs.append(cert)
        console.print(f"  [green]✓[/green] cert {cert.certificate_id[:8]}…  issuer={cert.issuer[:16]}…")

    if new_certs:
        agent.certificates.extend(new_certs)
        agent.save(state_path, pw)
        console.print(f"\n[green]{len(new_certs)} certificate(s) saved.[/green]")
    else:
        console.print("\nNo new certificates to save.")


# ---------------------------------------------------------------------------
# agent finalize
# ---------------------------------------------------------------------------

@agent_app.command("finalize")
def agent_finalize(
    store_url: str = typer.Argument(help="URL of the coordination store where your mailbox lives."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Poll for pending acceptances, finalize handshakes, and save certificates."""
    from .handshake import receive_acceptances, finalize_handshake, send_certificate
    from .store_client import RemoteStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Purge expired invites before processing — saves state immediately if any removed
    expired = agent.purge_expired_invites()
    if expired:
        console.print(f"[yellow]Removed {len(expired)} expired invite(s).[/yellow]")
        agent.save(state_path, pw)

    if not agent.pending_invites:
        console.print("No pending invites to finalize.")
        return

    remote = RemoteStore(store_url)
    acceptances = receive_acceptances(agent.store_key, remote)

    if not acceptances:
        console.print("No pending acceptances in mailbox yet.")
        remote.close()
        return

    console.print(f"Found [cyan]{len(acceptances)}[/cyan] acceptance(s).")

    # Match each acceptance to a pending invite by invitation_id
    pending_by_id = {pi.invite.invitation_id: pi for pi in agent.pending_invites}
    finalized_invite_ids = []
    new_certs = []

    for acceptance, acc_valid in acceptances:
        if not acc_valid:
            console.print(f"  [yellow]Skipping[/yellow] acceptance with invalid signature.")
            continue
        pi = pending_by_id.get(acceptance.invitation_id)
        if pi is None:
            console.print(f"  [yellow]Skipping[/yellow] acceptance for unknown invite {acceptance.invitation_id[:16]}…")
            continue

        try:
            peer_store_pub = bytes.fromhex(pi.peer_store_pub_hex)
            cert = finalize_handshake(acceptance, pi.invite, agent.identity_key)
            send_certificate(cert, peer_store_pub, remote)
            new_certs.append(cert)
            finalized_invite_ids.append(pi.invite.invitation_id)
            console.print(f"  [green]✓[/green] Finalized with {acceptance.responder.get('public_key', '?')[:16]}… → cert {cert.certificate_id[:8]}…")
        except Exception as exc:
            console.print(f"  [red]Failed[/red] to finalize {acceptance.invitation_id[:16]}…: {exc}")

    remote.close()

    # Update agent state: remove finalized invites, add new certs
    agent.pending_invites = [
        pi for pi in agent.pending_invites
        if pi.invite.invitation_id not in finalized_invite_ids
    ]
    agent.certificates.extend(new_certs)
    agent.save(state_path, pw)

    if new_certs:
        console.print(f"\n[green]{len(new_certs)} certificate(s) saved.[/green]")
    else:
        console.print("\nNo handshakes finalized.")


# ---------------------------------------------------------------------------
# agent sync
# ---------------------------------------------------------------------------

@agent_app.command("sync")
def agent_sync(
    store_url: str = typer.Argument(help="URL of the coordination store where your mailbox lives."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """One-shot sync: receive certs, pull revocations, purge expired invites.

    Equivalent to running receive-certs + pull-revocations + purge of expired
    pending invites in sequence.  Saves state once at the end if anything changed.
    """
    from .handshake import receive_certificates
    from .revoke import receive_revocations
    from .store_client import RemoteStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    remote = RemoteStore(store_url)
    changed = False

    # 1. Receive certificates
    try:
        cert_pairs = receive_certificates(agent.store_key, remote)
        existing_ids = {c.certificate_id for c in agent.certificates}
        new_certs = [c for c, valid in cert_pairs if valid and c.certificate_id not in existing_ids]
        if new_certs:
            agent.certificates.extend(new_certs)
            changed = True
            console.print(f"  [green]+{len(new_certs)} cert(s)[/green]")
        else:
            console.print(f"  No new certificates.")
    except Exception as exc:
        console.print(f"  [yellow]Certs:[/yellow] {exc}")

    # 2. Pull revocations
    try:
        applied = receive_revocations(agent.store_key, remote, agent.revocation_list)
        if applied:
            changed = True
            console.print(f"  [green]+{len(applied)} revocation(s) applied[/green]")
        else:
            console.print(f"  No revocation notices.")
    except Exception as exc:
        console.print(f"  [yellow]Revocations:[/yellow] {exc}")

    remote.close()

    # 3. Purge expired invites
    expired = agent.purge_expired_invites()
    if expired:
        changed = True
        console.print(f"  [yellow]Removed {len(expired)} expired invite(s).[/yellow]")

    if changed:
        agent.save(state_path, pw)
        console.print(f"\n[green]Sync complete — state saved.[/green]")
    else:
        console.print(f"\nSync complete — nothing changed.")


# ---------------------------------------------------------------------------
# agent pull-revocations
# ---------------------------------------------------------------------------

@agent_app.command("pull-revocations")
def agent_pull_revocations(
    store_url: str = typer.Argument(help="URL of the coordination store where your mailbox lives."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Drain revocation notices from your mailbox and apply them to the local list.

    Each valid, signed notice is applied to the agent's revocation list so that
    subsequent 'validate_request' calls will deny the revoked token or certificate.
    State is saved after any new notices are applied.
    """
    from .revoke import receive_revocations
    from .store_client import RemoteStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    remote = RemoteStore(store_url)
    try:
        applied = receive_revocations(agent.store_key, remote, agent.revocation_list)
    except Exception as exc:
        console.print(f"[red]Failed to pull revocations:[/red] {exc}")
        remote.close()
        raise typer.Exit(1)
    finally:
        remote.close()

    if not applied:
        console.print("No revocation notices in mailbox.")
        return

    console.print(f"Applied [cyan]{len(applied)}[/cyan] revocation notice(s):")
    for notice in applied:
        console.print(
            f"  {notice.subject_type} {notice.subject_id[:16]}…"
            f"  by={notice.issuer_pub_key[:16]}…"
            + (f"  reason={notice.reason}" if notice.reason else "")
        )

    agent.save(state_path, pw)
    console.print(f"\n[green]Revocation list updated.[/green]")


# ---------------------------------------------------------------------------
# agent renew
# ---------------------------------------------------------------------------

@agent_app.command("renew")
def agent_renew(
    cert_id_prefix: str = typer.Argument(help="Certificate ID (or unique prefix) to renew."),
    store_url: str = typer.Argument(help="URL of the coordination store to send the invite through."),
    peer_store_pub: Optional[str] = typer.Option(
        None, "--peer-store-pub",
        help="Peer's X25519 store pubkey (hex 32 bytes). Required unless --peer-store-url is given.",
    ),
    peer_store_url: Optional[str] = typer.Option(
        None, "--peer-store-url",
        help="URL of the peer's coordination store — auto-discovers their pubkey via GET /info.",
    ),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Renew a certificate by sending a fresh invite with the same capabilities.

    The peer must accept the invite (proxion agent accept) and you must finalize
    (proxion agent finalize) to complete the renewal.  The old certificate remains
    valid until it expires; the new one is issued with a fresh 90-day lifetime.
    """
    from .handshake import create_invite, send_invite
    from .persist import PendingInvite
    from .store_client import RemoteStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Find the certificate to renew
    matches = [c for c in agent.certificates if c.certificate_id.startswith(cert_id_prefix)]
    if not matches:
        console.print(f"[red]No certificate found with ID prefix:[/red] {cert_id_prefix}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous prefix — {len(matches)} certificates match.[/red]")
        for c in matches:
            console.print(f"  {c.certificate_id}")
        raise typer.Exit(1)
    cert = matches[0]

    # Resolve peer store pubkey
    if peer_store_pub:
        raw_peer_pub = bytes.fromhex(peer_store_pub.strip())
        if len(raw_peer_pub) != 32:
            console.print("[red]--peer-store-pub must be 32 bytes (64 hex chars).[/red]")
            raise typer.Exit(1)
        peer_key_hex = peer_store_pub.strip()
    elif peer_store_url:
        import httpx as _httpx
        try:
            resp = _httpx.get(f"{peer_store_url.rstrip('/')}/info", timeout=5.0)
            resp.raise_for_status()
            peer_key_hex = resp.json().get("store_pubkey")
            if not peer_key_hex:
                console.print("[red]Peer store returned no store_pubkey in /info.[/red]")
                raise typer.Exit(1)
            raw_peer_pub = bytes.fromhex(peer_key_hex.strip())
        except Exception as exc:
            console.print(f"[red]Could not discover peer pubkey:[/red] {exc}")
            raise typer.Exit(1)
    else:
        console.print("[red]Provide --peer-store-pub or --peer-store-url.[/red]")
        raise typer.Exit(1)

    # Create a new invite with the same capabilities as the expiring cert
    invite = create_invite(agent.identity_key, agent.store_pub_bytes, cert.capabilities)

    remote = RemoteStore(store_url)
    try:
        send_invite(invite, raw_peer_pub, remote)
    except Exception as exc:
        console.print(f"[red]Failed to send renewal invite:[/red] {exc}")
        remote.close()
        raise typer.Exit(1)
    finally:
        remote.close()

    agent.pending_invites.append(
        PendingInvite(invite=invite, peer_store_pub_hex=peer_key_hex)
    )
    agent.save(state_path, pw)

    console.print(f"[green]Renewal invite sent.[/green]")
    console.print(f"  Renewing cert : {cert.certificate_id[:16]}…")
    console.print(f"  New invite ID : [cyan]{invite.invitation_id}[/cyan]")
    console.print(
        f"  Capabilities  : "
        + (", ".join(f"{c.can}:{c.with_}" for c in cert.capabilities) or "(none)")
    )
    console.print("Run [bold]proxion agent finalize[/bold] once the peer has accepted.")


# ---------------------------------------------------------------------------
# agent revoke
# ---------------------------------------------------------------------------

@agent_app.command("revoke")
def agent_revoke(
    cert_id_prefix: str = typer.Argument(help="Certificate ID (or unique prefix) to revoke."),
    store_url: str = typer.Argument(help="URL of the coordination store to post the notice through."),
    peer_store_pub: Optional[str] = typer.Option(
        None, "--peer-store-pub",
        help="Peer's X25519 store pubkey (hex 32 bytes).  Required unless --peer-store-url is given.",
    ),
    peer_store_url: Optional[str] = typer.Option(
        None, "--peer-store-url",
        help="URL of the peer's coordination store — auto-discovers their store pubkey via GET /info.",
    ),
    reason: Optional[str] = typer.Option(None, "--reason", help="Optional free-text revocation reason."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Revoke a certificate locally and broadcast the notice to the peer's mailbox."""
    from .revoke import revoke_and_broadcast
    from .store_client import RemoteStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Find the certificate by ID prefix
    matches = [c for c in agent.certificates if c.certificate_id.startswith(cert_id_prefix)]
    if not matches:
        console.print(f"[red]No certificate found with ID prefix:[/red] {cert_id_prefix}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous prefix — {len(matches)} certificates match:[/red]")
        for c in matches:
            console.print(f"  {c.certificate_id}")
        raise typer.Exit(1)
    cert = matches[0]

    # Resolve the peer's X25519 store pubkey
    if peer_store_pub:
        raw_peer_pub = bytes.fromhex(peer_store_pub.strip())
        if len(raw_peer_pub) != 32:
            console.print("[red]--peer-store-pub must be 32 bytes (64 hex chars).[/red]")
            raise typer.Exit(1)
    elif peer_store_url:
        import httpx as _httpx
        try:
            resp = _httpx.get(f"{peer_store_url.rstrip('/')}/info", timeout=5.0)
            resp.raise_for_status()
            key_hex = resp.json().get("store_pubkey")
            if not key_hex:
                console.print("[red]Peer store returned no store_pubkey in /info.[/red]")
                raise typer.Exit(1)
            raw_peer_pub = bytes.fromhex(key_hex.strip())
        except Exception as exc:
            console.print(f"[red]Could not discover peer pubkey from {peer_store_url}:[/red] {exc}")
            raise typer.Exit(1)
    else:
        console.print("[red]Provide --peer-store-pub or --peer-store-url.[/red]")
        raise typer.Exit(1)

    remote = RemoteStore(store_url)
    try:
        notice = revoke_and_broadcast(
            subject=cert,
            issuer_priv=agent.identity_key,
            peer_store_pub_keys=[raw_peer_pub],
            store=remote,
            revocation_list=agent.revocation_list,
            reason=reason,
        )
    except Exception as exc:
        console.print(f"[red]Revocation failed:[/red] {exc}")
        remote.close()
        raise typer.Exit(1)
    finally:
        remote.close()

    agent.save(state_path, pw)

    console.print(f"[green]Certificate revoked.[/green]")
    console.print(f"  Certificate : {cert.certificate_id[:16]}…")
    console.print(f"  Notice ID   : {notice.notice_id}")
    if reason:
        console.print(f"  Reason      : {reason}")
    console.print(f"[dim]Revocation notice posted to peer mailbox.[/dim]")


# ---------------------------------------------------------------------------
# agent issue-token
# ---------------------------------------------------------------------------

@agent_app.command("issue-token")
def agent_issue_token(
    cert_id_prefix: str = typer.Argument(help="Certificate ID prefix (≥8 chars)."),
    validator_url: str = typer.Argument(help="Validator server base URL."),
    mgmt_token: Optional[str] = typer.Option(None, "--mgmt-token", envvar="PROXION_VALIDATOR_TOKEN"),
    ttl: int = typer.Option(3600, "--ttl", help="Token TTL in seconds."),
    aud: str = typer.Option("", "--aud", help="Audience claim."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Issue a signed token from a certificate via a validator server."""
    import httpx as _httpx
    import json as _json

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Find certificate by prefix
    matches = [c for c in agent.certificates if c.certificate_id.startswith(cert_id_prefix)]
    if not matches:
        console.print(f"[red]No certificate found with ID prefix:[/red] {cert_id_prefix}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous prefix — {len(matches)} certificates match.[/red]")
        for c in matches:
            console.print(f"  {c.certificate_id}")
        raise typer.Exit(1)
    cert = matches[0]

    # Build permissions
    permissions = [[c.can, c.with_] for c in cert.capabilities]

    # Build request body
    body = {
        "holder_pub_key_hex": agent.identity_pub_bytes.hex(),
        "permissions": permissions,
        "ttl_seconds": ttl,
        "aud": aud,
    }

    # POST to validator
    headers = {}
    if mgmt_token:
        headers["Authorization"] = f"Bearer {mgmt_token}"

    try:
        resp = _httpx.post(
            f"{validator_url.rstrip('/')}/token",
            json=body,
            headers=headers,
            timeout=5.0,
        )
        resp.raise_for_status()
        typer.echo(_json.dumps(resp.json(), indent=2))
    except _httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} {exc.response.text}", err=True)
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(f"Connection error: {exc}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# agent issue-from-cert
# ---------------------------------------------------------------------------

@agent_app.command("issue-from-cert")
def agent_issue_from_cert(
    cert_id_prefix: str = typer.Argument(..., help="Certificate ID prefix (≥8 chars)."),
    permissions: list[str] = typer.Option(..., "--perm", help="Permission as 'action:resource'. Repeatable."),
    ttl: int = typer.Option(3600, "--ttl", help="Token TTL in seconds."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Mint a cert-bounded capability token locally (no HTTP required)."""
    import json as _json
    import datetime as _dt

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Find certificate by prefix
    matches = [c for c in agent.certificates if c.certificate_id.startswith(cert_id_prefix)]
    if not matches:
        console.print(f"[red]No certificate found with ID prefix:[/red] {cert_id_prefix}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous prefix — {len(matches)} certificates match.[/red]")
        for c in matches:
            console.print(f"  {c.certificate_id}")
        raise typer.Exit(1)
    cert = matches[0]

    # Parse permissions
    parsed_perms = []
    for perm_str in permissions:
        parts = perm_str.split(":", 1)
        if len(parts) != 2:
            console.print(f"[red]Invalid permission format:[/red] {perm_str}")
            console.print("[yellow]Expected: action:resource[/yellow]")
            raise typer.Exit(1)
        parsed_perms.append(tuple(parts))

    # Issue the token locally
    from .certtoken import issue_from_certificate, CertTokenError

    now_dt = _dt.datetime.now(_dt.timezone.utc)
    try:
        token = issue_from_certificate(
            cert=cert,
            requested_permissions=parsed_perms,
            holder_pub_key=agent.identity_key.public_key(),
            signing_key=agent.signing_key_bytes,
            ttl_seconds=ttl,
            now=now_dt,
        )
    except CertTokenError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Serialize and output the token
    wire = {
        "token_id": token.token_id,
        "permissions": sorted([list(p) for p in token.permissions]),
        "exp": token.exp.isoformat(),
        "aud": token.aud,
        "holder_key_fingerprint": token.holder_key_fingerprint,
        "signing_alg": token.alg,
        "signature": token.signature,
        "caveats": [c.id for c in token.caveats],
        "issued_at": token.exp.isoformat(),
    }
    if token.parent_token_id:
        wire["parent_token_id"] = token.parent_token_id
    typer.echo(_json.dumps(wire, indent=2))


# ---------------------------------------------------------------------------
# agent prune-certs
# ---------------------------------------------------------------------------

@agent_app.command("prune-certs")
def agent_prune_certs(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be removed without modifying state."),
):
    """Remove expired and revoked certificates from local state."""
    from .revocation import certificate_revocation_id
    import time as _time
    import datetime as _dt

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    now_ts = _time.time()
    now_dt = _dt.datetime.now(_dt.timezone.utc)

    to_remove = []
    for cert in agent.certificates:
        if cert.expires_at < now_ts:
            to_remove.append((cert, "expired"))
        else:
            rev_id = certificate_revocation_id(cert)
            if agent.revocation_list.is_revoked(rev_id, now_dt):
                to_remove.append((cert, "revoked"))

    if not to_remove:
        typer.echo("Nothing to prune.")
        return

    expired_n = sum(1 for _, r in to_remove if r == "expired")
    revoked_n = sum(1 for _, r in to_remove if r == "revoked")
    parts = []
    if expired_n:
        parts.append(f"{expired_n} expired")
    if revoked_n:
        parts.append(f"{revoked_n} revoked")
    summary = ", ".join(parts)

    if dry_run:
        typer.echo(f"Would prune {len(to_remove)} certificate(s). ({summary})")
        return

    remove_ids = {cert.certificate_id for cert, _ in to_remove}
    agent.certificates = [c for c in agent.certificates if c.certificate_id not in remove_ids]
    agent.save(state_path, pw)
    typer.echo(f"Pruned {len(to_remove)} certificate(s). ({summary})")


# ---------------------------------------------------------------------------
# agent export-identity
# ---------------------------------------------------------------------------

@agent_app.command("export-identity")
def agent_export_identity(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
    store_url: Optional[str] = typer.Option(
        None, "--store-url",
        help="Your proxion store serve URL to embed in the identity card.",
    ),
    pod_url: Optional[str] = typer.Option(
        None, "--pod-url",
        help="Your Solid Pod base URL to embed in the identity card.",
    ),
):
    """Print a JSON identity card (pubkeys + mailbox ID) for sharing with peers."""
    from .sealed import mailbox_id_for
    import json as _json

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    card = {
        "version": "1",
        "identity_pub_hex": agent.identity_pub_bytes.hex(),
        "store_pub_hex": agent.store_pub_bytes.hex(),
        "mailbox_id_hex": mailbox_id_for(agent.store_pub_bytes),
    }
    if store_url:
        card["store_url"] = store_url
    if pod_url:
        card["pod_url"] = pod_url
    card_json = _json.dumps(card, indent=2)

    if output:
        out_path = __import__("pathlib").Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(card_json, encoding="utf-8")
        typer.echo(f"Identity card written to {out_path}", err=True)
    else:
        typer.echo(card_json)


# ---------------------------------------------------------------------------
# agent backup / agent restore-identity (E1)
# ---------------------------------------------------------------------------

@agent_app.command("backup")
def agent_backup(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
    output: str = typer.Option(
        "proxion-recovery-kit.json", "--output", "-o",
        help="File to write the encrypted recovery kit to.",
    ),
    backup_passphrase: Optional[str] = typer.Option(
        None, "--backup-passphrase",
        help="Recovery code / passphrase that encrypts the kit (prompted if omitted).",
    ),
):
    """Export an encrypted recovery kit (both private keys) to a file.

    The kit is the same format the web client downloads from Settings and can
    be restored with `proxion agent restore-identity` or the web UI.
    """
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    bp = _get_passphrase(backup_passphrase, prompt="Recovery code / backup passphrase")
    blob = agent.export_backup(passphrase=bp)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(blob)
    console.print(f"[green]Recovery kit written to[/green] {out_path}")
    console.print("[yellow]Store the recovery code safely — the kit is useless without it.[/yellow]")


@agent_app.command("restore-identity")
def agent_restore_identity(
    input: str = typer.Argument(..., help="Path to a recovery kit (proxion-recovery-kit*.json)."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
    backup_passphrase: Optional[str] = typer.Option(
        None, "--backup-passphrase",
        help="Recovery code / passphrase the kit was encrypted with (prompted if omitted).",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Overwrite an existing state file without asking.",
    ),
):
    """Restore identity keys from a recovery kit into the agent state file."""
    from .persist import AgentState, PersistError

    kit_path = Path(input)
    if not kit_path.is_file():
        console.print(f"[red]Error:[/red] no such file: {kit_path}")
        raise typer.Exit(1)

    state_path = _resolve_state(state)
    if state_path.exists() and not force:
        overwrite = typer.confirm(
            f"State file {state_path} already exists — overwrite its identity keys?"
        )
        if not overwrite:
            raise typer.Exit(1)

    bp = _get_passphrase(backup_passphrase, prompt="Recovery code / backup passphrase")
    try:
        agent = AgentState.import_backup(kit_path.read_bytes(), passphrase=bp)
    except PersistError as exc:
        console.print(f"[red]Restore failed:[/red] {exc}")
        raise typer.Exit(1)

    pw = _get_passphrase(passphrase)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    agent.save(state_path, pw)
    console.print(f"[green]Identity restored to[/green] {state_path}")
    console.print(f"  Identity pubkey : [cyan]{agent.identity_pub_bytes.hex()}[/cyan]")
    console.print(f"  Store pubkey    : [cyan]{agent.store_pub_bytes.hex()}[/cyan]")


# ---------------------------------------------------------------------------
# agent status
# ---------------------------------------------------------------------------

@agent_app.command("rotate-store-key")
def agent_rotate_store_key(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Generate a new X25519 store key and save it.  Returns the old pubkey."""
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    old_pub = agent.store_pub_bytes.hex()
    agent.rotate_store_key()
    agent.save(state_path, pw)

    console.print(f"[green]Store key rotated.[/green]")
    console.print(f"  Old pubkey : [dim]{old_pub}[/dim]")
    console.print(f"  New pubkey : [cyan]{agent.store_pub_bytes.hex()}[/cyan]")
    console.print("[yellow]Drain your old mailbox before discarding the old key.[/yellow]")


@agent_app.command("rotate-identity-key")
def agent_rotate_identity_key(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Generate a new Ed25519 identity key and save it.

    Existing certificates remain valid.  Peers will need a fresh handshake
    to verify future signatures from this agent.
    """
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    old_pub = agent.identity_pub_bytes.hex()
    agent.rotate_identity_key()
    agent.save(state_path, pw)

    console.print(f"[green]Identity key rotated.[/green]")
    console.print(f"  Old pubkey : [dim]{old_pub}[/dim]")
    console.print(f"  New pubkey : [cyan]{agent.identity_pub_bytes.hex()}[/cyan]")
    console.print("[yellow]Run a fresh handshake with peers who need to verify your new identity.[/yellow]")


@agent_app.command("status")
def agent_status(
    store_url: Optional[str] = typer.Argument(
        default=None,
        help="URL of the coordination store (optional — adds live mailbox stats when provided).",
    ),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Show agent keys, pending invites, certificates, and (optionally) live mailbox stats."""
    import datetime as _dt
    from .sealed import mailbox_id_for

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    mailbox_id = mailbox_id_for(agent.store_pub_bytes)

    # --- Local state ---------------------------------------------------------
    console.print(f"\n[bold]Agent state:[/bold] {state_path}")
    console.print(f"  Identity pubkey : [cyan]{agent.identity_pub_bytes.hex()}[/cyan]")
    console.print(f"  Store pubkey    : [cyan]{agent.store_pub_bytes.hex()}[/cyan]")
    console.print(f"  Mailbox ID      : [cyan]{mailbox_id}[/cyan]")

    # --- Pending invites -----------------------------------------------------
    now_ts = _dt.datetime.now(_dt.timezone.utc).timestamp()
    console.print(f"\n[bold]Pending invites:[/bold] {len(agent.pending_invites)}")
    for pi in agent.pending_invites:
        inv = pi.invite
        age_s = now_ts - pi.sent_at
        expired = inv.expires_at < now_ts
        exp_label = "  [red](EXPIRED)[/red]" if expired else ""
        console.print(
            f"  {inv.invitation_id[:16]}…  peer={pi.peer_store_pub_hex[:16]}…"
            f"  sent {age_s:.0f}s ago{exp_label}"
        )

    # --- Certificates --------------------------------------------------------
    console.print(f"\n[bold]Certificates:[/bold] {len(agent.certificates)}")
    for cert in agent.certificates:
        exp = _dt.datetime.fromtimestamp(cert.expires_at).strftime("%Y-%m-%d")
        caps = ", ".join(f"{c.can}:{c.with_}" for c in cert.capabilities) or "(none)"
        console.print(
            f"  {cert.certificate_id[:8]}…  issuer={cert.issuer[:16]}…"
            f"  caps={caps}  expires={exp}"
        )

    # --- Live mailbox stats (only when store_url provided) -------------------
    if store_url:
        from .store_client import RemoteStore
        remote = RemoteStore(store_url)
        try:
            info = remote.peek(mailbox_id)
            console.print(f"\n[bold]Mailbox stats[/bold] ({store_url}):")
            console.print(f"  Pending messages : {info['count']}")
            console.print(f"  Total bytes      : {info['bytes']}")
            if info["oldest_age_s"] is not None:
                console.print(f"  Oldest message   : {info['oldest_age_s']:.1f}s ago")
        except Exception as exc:
            console.print(f"\n[yellow]Could not reach store:[/yellow] {exc}")
        finally:
            remote.close()


# ---------------------------------------------------------------------------
# agent check-token
# ---------------------------------------------------------------------------

@agent_app.command("check-token")
def agent_check_token(
    validator_url: str = typer.Argument(..., help="Validator server base URL."),
    token_file: str = typer.Option(..., "--token-file", "-f", help="Path to token JSON file. Use '-' for stdin."),
    resource: str = typer.Option(..., "--resource", "-r"),
    action: str = typer.Option(..., "--action", "-a"),
    aud: str = typer.Option(..., "--aud"),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Validate a capability token against a running validator server."""
    import json
    import secrets

    # Load state to get identity key for PoP
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Read token JSON
    if token_file == "-":
        import sys
        token_json_str = sys.stdin.read()
    else:
        try:
            token_path = Path(token_file)
            token_json_str = token_path.read_text(encoding="utf-8")
        except Exception as e:
            console.print(f"[red]Error reading token file:[/red] {e}")
            raise typer.Exit(1)

    try:
        token_wire = json.loads(token_json_str)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error parsing token JSON:[/red] {e}")
        raise typer.Exit(1)

    # Generate nonce and PoP
    from .pop import sign_challenge
    import secrets
    nonce = secrets.token_hex(16)
    try:
        token_id = token_wire["token_id"]
        proof = sign_challenge(agent.identity_key, token_id, nonce)
    except Exception as e:
        console.print(f"[red]Error generating proof:[/red] {e}")
        raise typer.Exit(1)

    # Build request
    ctx = {
        "action": action,
        "resource": resource,
        "aud": aud,
        "device_nonce": nonce,
    }
    request_body = {
        "token": token_wire,
        "proof": {
            "public_key_bytes": proof.public_key_bytes.hex(),
            "nonce": proof.nonce,
            "signature": proof.signature.hex(),
        },
        "context": ctx,
    }

    # POST to validator
    try:
        import httpx
    except ImportError:
        console.print("[red]httpx is required.[/red] Install with: pip install httpx")
        raise typer.Exit(1)

    try:
        url = f"{validator_url.rstrip('/')}/validate"
        with httpx.Client() as client:
            resp = client.post(url, json=request_body)
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        console.print(f"[red]Error contacting validator:[/red] {e}")
        raise typer.Exit(1)

    # Display result
    if result.get("allowed"):
        console.print("[green]ALLOWED[/green]")
    else:
        reason = result.get("reason", "unknown")
        console.print(f"[red]DENIED[/red]: {reason}")
        raise typer.Exit(1)



# ---------------------------------------------------------------------------
# store mailbox — inspection subcommands
# ---------------------------------------------------------------------------

@mailbox_app.command("peek")
def mailbox_peek(
    mailbox_id: str = typer.Argument(help="Mailbox ID (hex) to inspect."),
    store_url: str = typer.Argument(help="URL of the coordination store."),
):
    """Show count, byte size, and age of the oldest message in a mailbox."""
    from .store_client import RemoteStore
    remote = RemoteStore(store_url)
    try:
        info = remote.peek(mailbox_id)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)
    finally:
        remote.close()

    console.print(f"\n[bold]Mailbox:[/bold] {mailbox_id}")
    console.print(f"  Messages : {info['count']}")
    console.print(f"  Bytes    : {info['bytes']}")
    if info["oldest_age_s"] is not None:
        console.print(f"  Oldest   : {info['oldest_age_s']:.1f}s ago")
    else:
        console.print(f"  Oldest   : (empty)")


@mailbox_app.command("list")
def mailbox_list(
    mailbox_id: str = typer.Argument(help="Mailbox ID (hex) to inspect."),
    store_url: str = typer.Argument(help="URL of the coordination store."),
):
    """List messages in a mailbox without removing them."""
    from .store_client import RemoteStore
    import datetime as _dt
    remote = RemoteStore(store_url)
    try:
        msgs = remote.list_all(mailbox_id)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)
    finally:
        remote.close()

    if not msgs:
        console.print(f"Mailbox {mailbox_id[:16]}… is empty.")
        return

    console.print(f"\n[bold]{len(msgs)} message(s)[/bold] in {mailbox_id[:16]}…\n")
    for sm in msgs:
        age = _dt.datetime.now(_dt.timezone.utc).timestamp() - sm.posted_at
        env = sm.envelope
        console.print(
            f"  {sm.message_id[:16]}…  "
            f"age={age:.0f}s  "
            f"bytes={len(env.ciphertext) if hasattr(env, 'ciphertext') else '?'}"
        )


@mailbox_app.command("drain")
def mailbox_drain(
    mailbox_id: str = typer.Argument(help="Mailbox ID (hex) to drain."),
    store_url: str = typer.Argument(help="URL of the coordination store."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
):
    """Remove and discard all messages from a mailbox."""
    from .store_client import RemoteStore
    if not yes:
        confirm = typer.confirm(f"Drain ALL messages from mailbox {mailbox_id[:16]}…?")
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(0)
    remote = RemoteStore(store_url)
    try:
        msgs = remote.take_all(mailbox_id)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)
    finally:
        remote.close()
    console.print(f"[green]Drained {len(msgs)} message(s).[/green]")


# ---------------------------------------------------------------------------
# store stats
# ---------------------------------------------------------------------------

@store_app.command("stats")
def store_stats(
    store_url: str = typer.Argument(help="URL of the coordination store."),
):
    """Print aggregate message store statistics."""
    import httpx as _httpx

    try:
        resp = _httpx.get(f"{store_url.rstrip('/')}/stats", timeout=5.0)
        resp.raise_for_status()
        d = resp.json()
        console.print(f"Mailboxes : {d['mailbox_count']}")
        console.print(f"Messages  : {d['total_messages']}")
        console.print(f"Bytes     : {d['total_bytes']}")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# agent pod-get
# ---------------------------------------------------------------------------

@agent_app.command("pod-get")
def agent_pod_get(
    stash_uri: str = typer.Argument(help="stash:// URI to fetch."),
    pod_url: str = typer.Option(..., "--pod-url", help="Solid Pod base URL."),
    cert_id_prefix: str = typer.Option(..., "--cert", help="Certificate ID prefix."),
    validator_signing_key: str = typer.Option(..., "--signing-key", help="Validator HMAC signing key (hex)."),
    aud: str = typer.Option("", "--aud"),
    ttl: int = typer.Option(3600, "--ttl"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Fetch a resource from a Solid Pod, enforcing capability token permissions."""
    import datetime as _dt
    from .certtoken import issue_from_certificate, CertTokenError
    from .solid import SolidResolver
    from .solid_client import SolidClient, SolidError
    from .solid_auth import AuthenticatedSolidClient

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Find certificate by prefix
    matches = [c for c in agent.certificates if c.certificate_id.startswith(cert_id_prefix)]
    if not matches:
        console.print(f"[red]No certificate found with ID prefix:[/red] {cert_id_prefix}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous prefix — {len(matches)} certificates match.[/red]")
        for c in matches:
            console.print(f"  {c.certificate_id}")
        raise typer.Exit(1)
    cert = matches[0]

    # Parse signing key
    try:
        signing_key = bytes.fromhex(validator_signing_key)
    except ValueError as exc:
        console.print(f"[red]Invalid signing key (must be hex):[/red] {exc}")
        raise typer.Exit(1)

    # Issue token from certificate
    try:
        now_dt = _dt.datetime.now(_dt.timezone.utc)
        token = issue_from_certificate(
            cert=cert,
            requested_permissions=[("read", stash_uri)],
            holder_pub_key=agent.identity_key.public_key(),
            signing_key=signing_key,
            ttl_seconds=ttl,
            now=now_dt,
        )
    except Exception as exc:
        console.print(f"[red]Failed to issue token:[/red] {exc}")
        raise typer.Exit(1)

    # Set up Solid client with authentication
    try:
        resolver = SolidResolver(pod_url)
        solid_client = SolidClient(resolver)
        auth_client = AuthenticatedSolidClient(
            solid_client,
            token,
            agent.identity_key,
            signing_key,
            aud=aud,
        )

        # Fetch the resource
        data = auth_client.get(stash_uri)

        # Write output
        if output:
            with open(output, "wb") as f:
                f.write(data)
            console.print(f"[green]✓[/green] Wrote {len(data)} bytes to {output}")
        else:
            import sys
            sys.stdout.buffer.write(data)

    except PermissionError as exc:
        console.print(f"[red]Permission denied:[/red] {exc}")
        raise typer.Exit(1)
    except SolidError as exc:
        console.print(f"[red]Solid error:[/red] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# agent pod-put
# ---------------------------------------------------------------------------

@agent_app.command("pod-put")
def agent_pod_put(
    stash_uri: str = typer.Argument(help="stash:// URI to write."),
    input_file: str = typer.Argument(help="Local file path to upload."),
    pod_url: str = typer.Option(..., "--pod-url"),
    cert_id_prefix: str = typer.Option(..., "--cert"),
    validator_signing_key: str = typer.Option(..., "--signing-key", help="Validator HMAC signing key (hex)."),
    aud: str = typer.Option("", "--aud"),
    ttl: int = typer.Option(3600, "--ttl"),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Upload a local file to a Solid Pod, enforcing capability token permissions."""
    import datetime as _dt
    from .certtoken import issue_from_certificate, CertTokenError
    from .solid import SolidResolver
    from .solid_client import SolidClient, SolidError
    from .solid_auth import AuthenticatedSolidClient
    from pathlib import Path

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Find certificate by prefix
    matches = [c for c in agent.certificates if c.certificate_id.startswith(cert_id_prefix)]
    if not matches:
        console.print(f"[red]No certificate found with ID prefix:[/red] {cert_id_prefix}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous prefix — {len(matches)} certificates match.[/red]")
        for c in matches:
            console.print(f"  {c.certificate_id}")
        raise typer.Exit(1)
    cert = matches[0]

    # Parse signing key
    try:
        signing_key = bytes.fromhex(validator_signing_key)
    except ValueError as exc:
        console.print(f"[red]Invalid signing key (must be hex):[/red] {exc}")
        raise typer.Exit(1)

    # Read input file
    try:
        input_path = Path(input_file)
        data = input_path.read_bytes()
    except FileNotFoundError:
        console.print(f"[red]File not found:[/red] {input_file}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Failed to read file:[/red] {exc}")
        raise typer.Exit(1)

    # Issue token from certificate
    try:
        now_dt = _dt.datetime.now(_dt.timezone.utc)
        token = issue_from_certificate(
            cert=cert,
            requested_permissions=[("write", stash_uri)],
            holder_pub_key=agent.identity_key.public_key(),
            signing_key=signing_key,
            ttl_seconds=ttl,
            now=now_dt,
        )
    except Exception as exc:
        console.print(f"[red]Failed to issue token:[/red] {exc}")
        raise typer.Exit(1)

    # Set up Solid client with authentication
    try:
        resolver = SolidResolver(pod_url)
        solid_client = SolidClient(resolver)
        auth_client = AuthenticatedSolidClient(
            solid_client,
            token,
            agent.identity_key,
            signing_key,
            aud=aud,
        )

        # Upload the resource
        auth_client.put(stash_uri, data)
        console.print(f"[green]✓[/green] Uploaded {len(data)} bytes to {stash_uri}")

    except PermissionError as exc:
        console.print(f"[red]Permission denied:[/red] {exc}")
        raise typer.Exit(1)
    except SolidError as exc:
        console.print(f"[red]Solid error:[/red] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@app.command("doctor")
def doctor(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
    store_url: Optional[str] = typer.Option(None, "--store-url", help="Override store URL to check."),
):
    """Run health checks: agent state, certs, store reachability."""
    import datetime as _dt
    import httpx as _httpx
    from rich.panel import Panel

    # Track results
    checks_passed = 0
    checks_failed = 0

    # 1. Agent state
    state_path = _resolve_state(state)
    try:
        pw = _get_passphrase(passphrase, "Passphrase")
        agent = _load_state(state_path, pw)
        identity_pub_hex = agent.identity_pub_bytes.hex()
        store_pub_hex = agent.store_pub_bytes.hex()
        console.print(
            f"[green]✓[/green] Agent state: [cyan]{identity_pub_hex[:16]}…[/cyan] / "
            f"[cyan]{store_pub_hex[:16]}…[/cyan]"
        )
        checks_passed += 1
    except Exception as exc:
        console.print(f"[red]✗[/red] Agent state: {exc}")
        checks_failed += 1
        raise typer.Exit(1)

    # 2. Certificates
    if not agent.certificates:
        console.print("[yellow]⚠[/yellow] Certificates: none")
    else:
        expired_count = sum(
            1 for c in agent.certificates
            if _dt.datetime.fromtimestamp(c.expires_at, tz=_dt.timezone.utc) < _dt.datetime.now(_dt.timezone.utc)
        )
        valid_count = len(agent.certificates) - expired_count
        status = f"{valid_count} valid"
        if expired_count > 0:
            status += f", {expired_count} expired"
        console.print(f"[green]✓[/green] Certificates: {status}")
        checks_passed += 1

        if expired_count > 0:
            for cert in agent.certificates:
                exp_dt = _dt.datetime.fromtimestamp(cert.expires_at, tz=_dt.timezone.utc)
                if exp_dt < _dt.datetime.now(_dt.timezone.utc):
                    console.print(f"  [red]  expired:[/red] {cert.certificate_id[:8]}…")

    # 3. Store reachability
    store_to_check = store_url or (agent.store_url if hasattr(agent, 'store_url') else None)
    if store_to_check:
        try:
            import time
            start = time.time()
            resp = _httpx.get(f"{store_to_check.rstrip('/')}/info", timeout=5.0)
            elapsed = (time.time() - start) * 1000
            resp.raise_for_status()
            store_info = resp.json()
            store_pubkey = store_info.get("store_pubkey", "?")
            console.print(
                f"[green]✓[/green] Store reachable ({elapsed:.0f}ms): "
                f"[cyan]{store_pubkey[:16]}…[/cyan]"
            )
            checks_passed += 1
        except Exception as exc:
            console.print(f"[red]✗[/red] Store reachable: {exc}")
            checks_failed += 1
    else:
        console.print("[yellow]⚠[/yellow] Store URL not configured (skip check)")

    # Summary
    console.print()
    if checks_failed == 0:
        console.print(f"[green]All checks passed ({checks_passed}/3)[/green]")
        raise typer.Exit(0)
    else:
        console.print(f"[red]{checks_failed} check(s) failed, {checks_passed} passed[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# agent lookup
# ---------------------------------------------------------------------------

@agent_app.command("lookup")
def agent_lookup(
    store_url: str = typer.Argument(help="URL of the peer's proxion store serve instance."),
    raw: bool = typer.Option(False, "--raw", help="Print raw JSON instead of formatted output."),
):
    """Fetch and display a peer identity card from their store URL."""
    import httpx as _httpx
    import json as _json

    base = store_url.rstrip("/")
    card = None
    tried = []

    for path in ["/.well-known/proxion-identity", "/info"]:
        url = f"{base}{path}"
        tried.append(url)
        try:
            resp = _httpx.get(url, timeout=5.0, follow_redirects=True)
            if resp.status_code == 200:
                card = resp.json()
                break
        except Exception:
            continue

    if card is None:
        console.print(f"[red]Could not reach peer store at {store_url}[/red]")
        console.print(f"  Tried: {tried}")
        raise typer.Exit(1)

    if raw:
        typer.echo(_json.dumps(card, indent=2))
        return

    console.print(f"\n[bold]Peer identity at:[/bold] {store_url}")
    console.print(f"  Identity pubkey : [cyan]{card.get('identity_pubkey') or '(not published)'}[/cyan]")
    console.print(f"  Store pubkey    : [cyan]{card.get('store_pubkey') or '(not published)'}[/cyan]")
    console.print(f"  Pod URL         : [cyan]{card.get('pod_url') or '(not published)'}[/cyan]")
    console.print(f"  Protocol version: {card.get('version', '?')}")
    console.print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# cert delegate
# ---------------------------------------------------------------------------

@cert_app.command("delegate")
def cert_delegate(
    cert_file: str = typer.Option(..., "--cert-file", help="JSON file of the root RelationshipCertificate."),
    issuer_key_file: str = typer.Option(..., "--issuer-key-file", help="AgentState JSON file (must be the cert issuer)."),
    device_pub_hex: str = typer.Option(..., "--device-pub-hex", help="Raw Ed25519 public key of the new device holder (hex)."),
    output: str = typer.Option(..., "--output", help="Write delegation cert JSON here."),
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Issue a delegation sub-certificate scoped to a device key."""
    import json as _json
    from .federation import RelationshipCertificate
    from .certtoken import delegate_cert, CertTokenError
    from .persist import AgentState

    try:
        with open(cert_file) as f:
            root_cert = RelationshipCertificate.from_dict(_json.load(f))
    except Exception as exc:
        console.print(f"[red]Failed to load cert file:[/red] {exc}")
        raise typer.Exit(1)

    pw = _get_passphrase(passphrase)
    try:
        agent = AgentState.load(Path(issuer_key_file), pw)
    except Exception as exc:
        console.print(f"[red]Failed to load issuer key file:[/red] {exc}")
        raise typer.Exit(1)

    try:
        device_pub_bytes = bytes.fromhex(device_pub_hex)
    except ValueError as exc:
        console.print(f"[red]Invalid --device-pub-hex:[/red] {exc}")
        raise typer.Exit(1)

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    device_pub_key = Ed25519PublicKey.from_public_bytes(device_pub_bytes)

    try:
        delegation_cert = delegate_cert(
            cert=root_cert,
            new_holder_pub_key=device_pub_key,
            issuer_identity_priv=agent.identity_key,
        )
    except CertTokenError as exc:
        console.print(f"[red]Delegation failed:[/red] {exc}")
        raise typer.Exit(1)

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_json.dumps(delegation_cert.to_dict(), indent=2))
    console.print(f"[green]Delegation cert written.[/green] ID: {delegation_cert.certificate_id}")


# ---------------------------------------------------------------------------
# cert renew
# ---------------------------------------------------------------------------

@cert_app.command("renew")
def cert_renew(
    cert_file: str = typer.Option(..., "--cert-file", help="JSON file of the RelationshipCertificate to renew."),
    issuer_key_file: str = typer.Option(..., "--issuer-key-file", help="AgentState JSON file (must be the cert issuer)."),
    ttl_days: int = typer.Option(90, "--ttl-days", help="New certificate TTL in days."),
    output: str = typer.Option(..., "--output", help="Write renewed cert JSON here."),
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Re-issue a certificate with a fresh expiry."""
    import json as _json
    from .federation import RelationshipCertificate
    from .certtoken import renew_cert, CertTokenError
    from .persist import AgentState

    try:
        with open(cert_file) as f:
            old_cert = RelationshipCertificate.from_dict(_json.load(f))
    except Exception as exc:
        console.print(f"[red]Failed to load cert file:[/red] {exc}")
        raise typer.Exit(1)

    pw = _get_passphrase(passphrase)
    try:
        agent = AgentState.load(Path(issuer_key_file), pw)
    except Exception as exc:
        console.print(f"[red]Failed to load issuer key file:[/red] {exc}")
        raise typer.Exit(1)

    try:
        renewed = renew_cert(old_cert, agent.identity_key, new_ttl_days=ttl_days)
    except CertTokenError as exc:
        console.print(f"[red]Renewal failed:[/red] {exc}")
        raise typer.Exit(1)

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_json.dumps(renewed.to_dict(), indent=2))
    import datetime as _dt
    exp_date = _dt.datetime.fromtimestamp(renewed.expires_at).date()
    console.print(f"[green]Cert renewed.[/green] New ID: {renewed.certificate_id}  Expires: {exp_date}")


# ---------------------------------------------------------------------------
# cert verify
# ---------------------------------------------------------------------------

@cert_app.command("verify")
def cert_verify(
    cert_file: str = typer.Option(..., "--cert-file", help="JSON file of the RelationshipCertificate."),
):
    """Verify the Ed25519 signature on a certificate file."""
    import json as _json
    from .federation import RelationshipCertificate
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        with open(cert_file) as f:
            cert = RelationshipCertificate.from_dict(_json.load(f))
    except Exception as exc:
        console.print(f"[red]Failed to load cert file:[/red] {exc}")
        raise typer.Exit(1)

    try:
        data = cert.to_dict()
        data.pop("signature", None)
        canonical = _json.dumps(data, sort_keys=True).encode()
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(cert.issuer))
        pub.verify(bytes.fromhex(cert.signature), canonical)
        console.print("Certificate signature: [green]VALID[/green]")
    except Exception:
        console.print("Certificate signature: [red]INVALID[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# cert info
# ---------------------------------------------------------------------------

@cert_app.command("info")
def cert_info(
    cert_file: str = typer.Option(..., "--cert-file", help="JSON file of the RelationshipCertificate."),
):
    """Display human-readable details of a certificate."""
    import json as _json
    import datetime as _dt
    from .federation import RelationshipCertificate

    try:
        with open(cert_file) as f:
            cert = RelationshipCertificate.from_dict(_json.load(f))
    except Exception as exc:
        console.print(f"[red]Failed to load cert file:[/red] {exc}")
        raise typer.Exit(1)

    now_ts = _dt.datetime.now(_dt.timezone.utc).timestamp()
    exp_dt = _dt.datetime.fromtimestamp(cert.expires_at, tz=_dt.timezone.utc)
    exp_str = exp_dt.strftime("%Y-%m-%d %H:%M UTC")
    exp_flag = "  [[red]EXPIRED[/red]]" if cert.expires_at < now_ts else "  [[green]valid[/green]]"

    console.print(f"Certificate: {cert.certificate_id[:12]}...")
    console.print(f"  Issuer   : {cert.issuer[:16]}...")
    console.print(f"  Subject  : {cert.subject[:16]}...")
    console.print(f"  Expires  : {exp_str}{exp_flag}")
    console.print("  Capabilities:")
    for cap in cert.capabilities:
        console.print(f"    {cap.can} {cap.with_}")


# ---------------------------------------------------------------------------
# ledger revoke
# ---------------------------------------------------------------------------

@ledger_app.command("revoke")
def ledger_revoke(
    cert_file: str = typer.Option(..., "--cert-file", help="JSON file of the RelationshipCertificate."),
    ledger_path: str = typer.Option(..., "--ledger-path", help="SQLite file used as the ledger store."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print count without modifying revocation list."),
):
    """Revoke all tokens in the cert's token ledger."""
    import json as _json
    from .federation import RelationshipCertificate
    from .revocation import RevocationList
    from .store_sqlite import SqliteStore

    try:
        with open(cert_file) as f:
            cert = RelationshipCertificate.from_dict(_json.load(f))
    except Exception as exc:
        console.print(f"[red]Failed to load cert file:[/red] {exc}")
        raise typer.Exit(1)

    store = SqliteStore(ledger_path)
    if dry_run:
        mailbox = f"token-ledger/{cert.certificate_id}"
        count = len(store.list_all(mailbox))
        console.print(f"Dry run: would revoke {count} token(s).")
    else:
        from .certtoken import revoke_tokens_via_ledger
        rl = RevocationList()
        count = revoke_tokens_via_ledger(cert, store, rl)
        console.print(f"Revoked {count} tokens.")


# ---------------------------------------------------------------------------
# chat dm send/read
# ---------------------------------------------------------------------------

@chat_dm_app.command("send")
def chat_dm_send(
    peer_store_url: str = typer.Argument(help="Peer's pod store URL."),
    message: str = typer.Argument(help="Message content to send."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Send a direct message to a peer."""
    from .css_setup import build_dpop_client
    from .messaging import compose, send
    import os
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    # Get CSS credentials
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    # Find cert for peer
    peer_cert = None
    for cert in agent.certificates:
        if cert.subject == peer_store_url or cert.issuer == peer_store_url:
            peer_cert = cert
            break
    
    if not peer_cert:
        console.print(f"[red]No cert with peer. Run:[/red] proxion agent invite {peer_store_url}")
        raise typer.Exit(1)
    
    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        msg = compose(agent.identity_key, peer_cert, message)
        send(client, msg, peer_cert)
        console.print("[green]Sent.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_dm_app.command("read")
def chat_dm_read(
    peer_store_url: str = typer.Argument(help="Peer's pod store URL."),
    since: Optional[str] = typer.Option(None, "--since", help="ISO timestamp to read messages since."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Read direct messages from a peer."""
    from .css_setup import build_dpop_client
    from .messaging import receive
    from .identity import fetch_identity
    import os
    from datetime import datetime
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    peer_cert = None
    for cert in agent.certificates:
        if cert.subject == peer_store_url or cert.issuer == peer_store_url:
            peer_cert = cert
            break
    
    if not peer_cert:
        console.print(f"[red]No cert with peer. Run:[/red] proxion agent invite {peer_store_url}")
        raise typer.Exit(1)
    
    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        since_dt = None
        if since:
            since_dt = datetime.fromisoformat(since)
        
        messages = receive(client, peer_cert, since=since_dt)
        if not messages:
            console.print("No messages.")
            return
        
        peer_identity = fetch_identity(client)
        peer_name = peer_identity.display_name if peer_identity else "Peer"
        
        for msg in messages:
            ts_str = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"[{ts_str}] {peer_name}: {msg.content}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_dm_app.command("list")
def chat_dm_list(
    with_did: bool = typer.Option(False, "--with-did/--no-with-did", help="Show DID column"),
    state: Optional[str] = _STATE_OPTION,
):
    """List all DM conversations."""
    import json
    
    state_path = _resolve_state(state)
    stash = _load_stash(state_path)
    
    # Load DM threads from stash
    try:
        dms_data_bytes = stash.get_sync("dms.json")
        if not dms_data_bytes:
            typer.echo("No DM conversations.")
            return
        dms = json.loads(dms_data_bytes.decode())
    except Exception:
        typer.echo("No DM conversations.")
        return
    
    if not dms:
        typer.echo("No DM conversations.")
        return
    
    table = Table(title="Direct Messages")
    table.add_column("Peer WebID", style="cyan")
    if with_did:
        table.add_column("DID", style="magenta")
    table.add_column("Last Message", style="dim")
    
    for peer_webid, data in dms.items():
        last_msg = data.get("last_message_iso", "(never)")[:10] if isinstance(data, dict) else "(unknown)"
        if with_did:
            # Try to get DID for peer - would need peerdb lookup
            did_str = data.get("did", "(unknown)") if isinstance(data, dict) else "(unknown)"
            table.add_row(peer_webid[:30] + "...", did_str[:44] + "...", last_msg)
        else:
            table.add_row(peer_webid[:30] + "...", last_msg)
    
    console.print(table)


# ---------------------------------------------------------------------------
# chat room commands
# ---------------------------------------------------------------------------

@chat_room_app.command("create")
def chat_room_create(
    name: str = typer.Argument(help="Room name."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Create a new room."""
    from .room import create_room
    from .room_store import RoomStore
    from .css_setup import build_dpop_client
    import os
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        room = create_room(client, name, agent.identity_key)
        
        # Save room to disk
        rooms_dir = state_path.parent / "rooms"
        store = RoomStore(rooms_dir)
        store.save_room(room)
        
        console.print(f"[green]Room created:[/green] {room.room_id}")
        console.print(f"  Name: {name}")
        console.print(f"  Pod: {room.pod_url}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_room_app.command("list")
def chat_room_list(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """List all saved rooms."""
    from .room_store import RoomStore
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    rooms_dir = state_path.parent / "rooms"
    if not rooms_dir.exists():
        console.print("No rooms saved.")
        return
    
    store = RoomStore(rooms_dir)
    rooms = store.list_rooms()
    
    if not rooms:
        console.print("No rooms saved.")
        return
    
    for room in rooms:
        console.print(f"  {room.room_id[:8]}… [{room.name}]")


@chat_room_app.command("send")
def chat_room_send(
    room_id: str = typer.Argument(help="Room ID."),
    message: str = typer.Argument(help="Message content."),
    encrypt: bool = typer.Option(False, "--encrypt", "-e", help="Encrypt the message."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Send a message to a room."""
    from .room_store import RoomStore
    from .room import send_to_room
    from .css_setup import build_dpop_client
    import os

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print(
            "[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required."
        )
        raise typer.Exit(1)

    try:
        rooms_dir = state_path.parent / "rooms"
        store = RoomStore(rooms_dir)
        room = store.load_room(room_id)

        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        # Auth client needs identity key for signing
        client.identity_key = agent.identity_key

        send_to_room(client, room, message, encrypt=encrypt)

        console.print("[green]Sent to room.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_room_app.command("read")
def chat_room_read(
    room_id: str = typer.Argument(help="Room ID."),
    since: Optional[str] = typer.Option(None, "--since", help="ISO timestamp."),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Number of messages."),
    before: Optional[str] = typer.Option(None, "--before", "-b", help="Exclude this message and newer."),
    decrypt: bool = typer.Option(True, "--decrypt/--no-decrypt", help="Decrypt messages."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Read messages from a room."""
    from .room_store import RoomStore
    from .room import read_room
    from .css_setup import build_dpop_client
    import os
    from datetime import datetime

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print(
            "[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required."
        )
        raise typer.Exit(1)

    try:
        rooms_dir = state_path.parent / "rooms"
        store = RoomStore(rooms_dir)
        room = store.load_room(room_id)

        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        since_ts = None
        if since:
            since_ts = int(datetime.fromisoformat(since).timestamp())

        messages = read_room(
            room, client, agent, since=since_ts, limit=limit, before=before, decrypt=decrypt
        )
        if not messages:
            console.print("No messages in room.")
            return

        for msg in messages:
            ts_str = datetime.fromtimestamp(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"[{ts_str}] <{msg.from_pub_hex[:8]}>: {msg.content}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_room_app.command("invite")
def chat_room_invite(
    room_id: str = typer.Argument(help="Room ID."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Generate an invite JSON for a room."""
    from .room_store import RoomStore
    from .room import invite_to_room

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    try:
        rooms_dir = state_path.parent / "rooms"
        store = RoomStore(rooms_dir)
        room = store.load_room(room_id)

        invite_json = invite_to_room(room, agent)

        console.print("[green]Invite generated:[/green]")
        console.print(invite_json)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_room_app.command("join")
def chat_room_join(
    invite_file: str = typer.Argument(help="Path to invite JSON file."),
    store_url: str = typer.Argument(help="Inviter's coordination store URL."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Join a room using an invite file."""
    from pathlib import Path
    from .room_store import RoomStore
    from .room import join_room
    from .store_client import RemoteStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    try:
        invite_path = Path(invite_file)
        if not invite_path.exists():
            console.print(f"[red]Invite file not found:[/red] {invite_file}")
            raise typer.Exit(1)

        invite_json = invite_path.read_text()

        remote = RemoteStore(store_url)
        webid_str = agent.identity_pub_bytes.hex()
        membership = join_room(invite_json, agent, webid_str, remote)
        remote.close()

        # Save membership to disk
        rooms_dir = state_path.parent / "rooms"
        store = RoomStore(rooms_dir)
        store.save_membership(membership)

        console.print(
            f"[green]Joined room:[/green] {membership.room.name} ({membership.room.room_id})"
        )
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_room_app.command("kick")
def chat_room_kick(
    room_id: str = typer.Argument(help="Room ID."),
    member_webid: str = typer.Argument(help="WebID of the member to remove."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Remove a member from a room (owner only)."""
    from .room_store import RoomStore
    from .room import remove_room_member, get_room_members
    from .css_setup import build_dpop_client
    import os

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS environment variables required.")
        raise typer.Exit(1)

    try:
        rooms_dir = state_path.parent / "rooms"
        store = RoomStore(rooms_dir)
        room = store.load_room(room_id)

        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        
        # 1. Fetch current members
        members = get_room_members(room, client)
        if member_webid not in members:
            console.print(f"[yellow]Member {member_webid} not found in room ACL.[/yellow]")
            return

        # 2. Remove member
        remove_room_member(client, room, member_webid, members)
        console.print(f"[green]Member {member_webid} removed from room {room_id}.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_room_app.command("delete-message")
def chat_room_delete_message(
    room_id: str = typer.Argument(help="Room ID."),
    message_id: str = typer.Argument(help="Message ID to delete."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Delete a message from a room (owner only)."""
    from .room_store import RoomStore
    from .room import delete_room_message
    from .css_setup import build_dpop_client
    import os

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS environment variables required.")
        raise typer.Exit(1)

    try:
        rooms_dir = state_path.parent / "rooms"
        store = RoomStore(rooms_dir)
        room = store.load_room(room_id)

        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        delete_room_message(client, room, message_id)
        console.print(f"[green]Message {message_id} deleted from room {room_id}.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# chat presence commands
# ---------------------------------------------------------------------------

@chat_presence_app.command("set")
def chat_presence_set(
    status: str = typer.Argument(help="Status: online, away, busy, offline."),
    status_text: Optional[str] = typer.Option(None, "--text", help="Custom status message, e.g. 'Playing Elden Ring'."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Set your presence status."""
    from .presence import set_presence
    from .css_setup import build_dpop_client
    import os

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)

    if status not in ["online", "away", "busy", "offline"]:
        console.print("[red]Invalid status.[/red] Use: online, away, busy, offline")
        raise typer.Exit(1)

    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        display_name = getattr(agent, "display_name", None) or "Unknown"
        set_presence(client, status, display_name, status_text=status_text)
        msg = f"[green]Presence set to:[/green] {status}"
        if status_text:
            msg += f" — {status_text}"
        console.print(msg)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_presence_app.command("get")
def chat_presence_get(
    peer_pod_url: str = typer.Argument(help="Peer's pod URL."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Get a peer's presence status."""
    from .presence import get_presence
    from .identity import fetch_identity
    from .css_setup import build_dpop_client
    import os
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        presence = get_presence(client, peer_pod_url)
        identity = fetch_identity(client)
        
        display_name = identity.display_name if identity else "User"
        console.print(f"{display_name} is {presence.status}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# chat file commands
# ---------------------------------------------------------------------------

@chat_file_app.command("send")
def chat_file_send(
    peer_pod_url: str = typer.Argument(help="Peer's pod URL."),
    local_path: str = typer.Argument(help="Local file path."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Send a file to a peer."""
    from .files import send_file
    from .css_setup import build_dpop_client
    import os
    from pathlib import Path
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    try:
        file_path = Path(local_path)
        if not file_path.exists():
            console.print(f"[red]File not found:[/red] {local_path}")
            raise typer.Exit(1)
        
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        filename = file_path.name
        content = file_path.read_bytes()
        
        send_file(client, filename, content, peer_pod_url)
        console.print(f"[green]Sent:[/green] {filename}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_file_app.command("list")
def chat_file_list(
    peer_pod_url: str = typer.Argument(help="Peer's pod URL."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """List files shared by a peer."""
    from .files import receive_files
    from .css_setup import build_dpop_client
    import os
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        attachments = receive_files(client, peer_pod_url)
        
        if not attachments:
            console.print("No files available.")
            return
        
        for att in attachments:
            size_kb = att.size // 1024 if att.size else 0
            console.print(f"  {att.filename} ({size_kb} KB) [{att.mime_type}]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_file_app.command("download")
def chat_file_download(
    peer_pod_url: str = typer.Argument(help="Peer's pod URL."),
    filename: str = typer.Argument(help="Filename to download."),
    out: Optional[str] = typer.Option(None, "--out", help="Destination path (default: current dir)."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Download a file from a peer."""
    from .files import download_file
    from .css_setup import build_dpop_client
    import os
    from pathlib import Path
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        content = download_file(client, filename, peer_pod_url)
        
        dest_path = Path(out) if out else Path(filename)
        dest_path.write_bytes(content)
        
        console.print(f"[green]Saved to:[/green] {dest_path}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# chat identity commands
# ---------------------------------------------------------------------------

@chat_identity_app.command("set")
def chat_identity_set(
    display_name: str = typer.Option(..., "--name", help="Display name."),
    bio: Optional[str] = typer.Option(None, "--bio", help="Short bio."),
    avatar_url: Optional[str] = typer.Option(None, "--avatar", help="Avatar URL."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Set your identity (display name and profile info)."""
    from .identity import publish_identity, IdentityCard
    from .css_setup import build_dpop_client
    import os
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        card = IdentityCard(
            display_name=display_name,
            avatar_url=avatar_url,
            bio=bio,
        )
        publish_identity(client, card)
        console.print("[green]Identity published.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_identity_app.command("get")
def chat_identity_get(
    peer_pod_url: str = typer.Argument(help="Peer's pod URL."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Get a peer's identity profile."""
    from .identity import fetch_identity
    from .css_setup import build_dpop_client
    import os
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
    
    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        identity = fetch_identity(client)
        
        console.print(f"[bold]{identity.display_name}[/bold]")
        if identity.bio:
            console.print(f"  {identity.bio}")
        if identity.avatar_url:
            console.print(f"  Avatar: {identity.avatar_url}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_app.command("search")
def chat_search(
    query: str = typer.Argument(..., help="Search query"),
    room: Optional[str] = typer.Option(None, "--room", help="Limit search to a specific room"),
    dm: Optional[str] = typer.Option(None, "--dm", help="Limit search to a DM with this peer"),
    state: Optional[str] = _STATE_OPTION,
):
    """Search for messages across all chats."""
    import asyncio
    from .search import search_all_threads
    
    # For now, just show a message that this is in progress
    # Full implementation would require loading all thread IDs from stash
    typer.echo(f"Searching for: {query}")
    
    if room:
        typer.echo(f"  in room: {room}")
    if dm:
        typer.echo(f"  in DM with: {dm}")
    
    typer.echo("[yellow]Search functionality requires full stash integration.[/yellow]")


# ---------------------------------------------------------------------------
# device commands
# ---------------------------------------------------------------------------

@device_app.command("link-create")
def device_link_create(
    new_pubkey: str = typer.Argument(help="The hex-encoded Ed25519 public key of the new device."),
    ttl: int = typer.Option(365, help="Certificate validity in days."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Create a device link invite (JSON) for a second device."""
    from .device import create_device_link
    import json
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    link = create_device_link(agent, new_pubkey, ttl)
    console.print("\n[bold]Device Link Invite (Share this JSON with the new device):[/bold]\n")
    console.print(json.dumps(link.to_dict(), indent=2))


@device_app.command("link-import")
def device_link_import(
    link_json: str = typer.Argument(help="The JSON device link invite string."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Import a device link invite and initialize this device."""
    from .device import accept_device_link, DeviceLink
    import json
    
    state_path = _resolve_state(state)
    if state_path.exists():
        console.print(f"[red]Error:[/red] State file already exists at {state_path}")
        raise typer.Exit(1)
        
    try:
        data = json.loads(link_json)
        link = DeviceLink.from_dict(data)
    except Exception as exc:
        console.print(f"[red]Error parsing link JSON:[/red] {exc}")
        raise typer.Exit(1)
        
    pw = _get_passphrase(passphrase, "New state passphrase")
    confirm = _get_passphrase(None, "Confirm passphrase")
    if pw != confirm:
        console.print("[red]Passphrases do not match.[/red]")
        raise typer.Exit(1)
        
    agent = accept_device_link(link, pw, state_path)
    console.print(f"[green]Successfully linked device![/green] WebID: {agent.css_webid}")


# ==============================================================================
# DID utilities (did_app)
# ==============================================================================

@did_app.command("show")
def did_show(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Show this agent's DID (did:key format)."""
    from .didkey import agent_did
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    did = agent_did(agent)
    typer.echo(did)


@did_app.command("resolve")
def did_resolve(
    did: str = typer.Argument(..., help="DID string (did:key:...)"),
):
    """Resolve a DID to its public key (hex-encoded)."""
    from .didkey import did_to_pub_key
    
    try:
        pub_key_bytes = did_to_pub_key(did)
        typer.echo(pub_key_bytes.hex())
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


@did_app.command("peers")
def did_peers(
    trusted: bool = typer.Option(False, "--trusted/--no-trusted", help="Show only trusted peers"),
    state: Optional[str] = _STATE_OPTION,
):
    """List known peers from the peer registry."""
    import asyncio
    from .peerdb import list_peers
    
    state_path = _resolve_state(state)
    stash = _load_stash(state_path)
    
    peers = asyncio.run(list_peers(stash, trusted_only=trusted))
    
    if not peers:
        typer.echo("No peers registered.")
        return
    
    table = Table(title="Peers")
    table.add_column("DID", style="cyan")
    table.add_column("Pod URL", style="magenta")
    table.add_column("Display Name", style="green")
    table.add_column("Trusted", style="yellow")
    table.add_column("Last Seen", style="dim")
    
    for peer in peers:
        table.add_row(
            peer.did[:44] + "...",
            peer.pod_url,
            peer.display_name or "(unnamed)",
            "✓" if peer.trusted else " ",
            peer.last_seen_iso[:10] if peer.last_seen_iso else "(unknown)",
        )
    
    console.print(table)


@did_app.command("trust")
def did_trust(
    did: str = typer.Argument(..., help="DID to trust"),
    pod_url: str = typer.Argument(..., help="Pod URL for the peer"),
    state: Optional[str] = _STATE_OPTION,
):
    """Mark a peer as trusted."""
    import asyncio
    from .peerdb import register_peer
    
    state_path = _resolve_state(state)
    stash = _load_stash(state_path)
    
    asyncio.run(register_peer(stash, did, pod_url, trusted=True))
    typer.echo(f"[green]Peer {did[:44]}... marked as trusted.[/green]")


# ==============================================================================
# Chat room search
# ==============================================================================

@chat_room_app.command("search")
def chat_room_search(
    query: str = typer.Argument(..., help="Search query (room name, description)"),
    state: Optional[str] = _STATE_OPTION,
):
    """Search for public rooms by name or description."""
    import asyncio
    from .room import search_rooms
    
    state_path = _resolve_state(state)
    stash = _load_stash(state_path)
    
    results = asyncio.run(search_rooms(stash, query))
    
    if not results:
        typer.echo(f"No rooms found matching '{query}'.")
        return
    
    table = Table(title=f"Room Search Results: '{query}'")
    table.add_column("Room ID", style="cyan")
    table.add_column("Display Name", style="green")
    
    for room in results:
        table.add_row(
            room.room_id[:20] + "...",
            room.display_name or "(untitled)",
        )
    
    console.print(table)


@chat_app.command("inbox")
def chat_inbox(
    limit: int = typer.Option(20, "--limit", "-l", help="Recent messages per thread."),
    unread_only: bool = typer.Option(False, "--unread-only", "-u", help="Only show messages not in read state."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """View recent messages from all rooms and DMs."""
    from .inbox import poll_inbox
    from .room import read_room
    from .css_setup import build_dpop_client
    from .readstate import ReadState
    from .room_store import RoomStore
    from datetime import datetime
    import os

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error: CSS environment variables required.[/red]")
        raise typer.Exit(1)

    try:
        read_state_path = state_path.parent / "readstate.json"
        read_state = ReadState.load(read_state_path)

        # 1. DMs
        console.print("[bold]Direct Messages:[/bold]")
        any_dms = False
        for cert in agent.certificates:
            client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
            # Polling inbox for DM
            entries = poll_inbox(client, cert, limit=limit)
            if unread_only:
                # This is a bit naive (compares to just the last read ID)
                # but matches the ReadState implementation
                last_msg = read_state.last_read(cert.certificate_id)
                # Assuming entries are chronological
                entries = [e for e in entries if e.message.message_id != last_msg]

            if not entries: continue
            any_dms = True
            console.print(f"  [cyan]DM: {cert.certificate_id[:8]}...[/cyan]")
            for e in entries:
                ts = datetime.fromtimestamp(e.message.timestamp).strftime("%H:%M")
                console.print(f"    {ts} <{e.message.from_pub_hex[:8]}>: {e.message.content}")
        if not any_dms:
            console.print("  (No recent DMs)")

        # 2. Rooms
        console.print("\n[bold]Rooms:[/bold]")
        room_store = RoomStore(state_path.parent / "rooms")
        rooms = room_store.list_rooms()
        any_rooms = False
        for room in rooms:
            try:
                membership = room_store.load_membership(room.room_id)
            except Exception:
                continue
            
            client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
            messages = read_room(membership.room, client, agent, limit=limit)
            
            if unread_only:
                last_msg = read_state.last_read(room.room_id)
                messages = [m for m in messages if m.message_id != last_msg]

            if not messages: continue
            any_rooms = True
            console.print(f"  [magenta]Room: {room.name}[/magenta]")
            for m in messages:
                ts = datetime.fromtimestamp(m.timestamp).strftime("%H:%M")
                console.print(f"    {ts} <{m.from_pub_hex[:8]}>: {m.content}")
        if not any_rooms:
            console.print("  (No recent room activity)")

    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_app.command("gateway")
def chat_gateway(
    host: str = typer.Option("127.0.0.1", help="Host to bind."),
    port: int = typer.Option(7474, help="Port to bind."),
    poll_interval: float = typer.Option(3.0, help="Poll interval in seconds."),
    push: bool = typer.Option(False, "--push", help="Enable push notifications (Solid Notifications)."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Run the WebSocket gateway for real-time messaging."""
    from .gateway import GatewayConfig, run_gateway
    from .css_setup import build_dpop_client
    from .room_store import RoomStore
    from .readstate import ReadState
    import os
    import asyncio
    
    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)
    
    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")
    
    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)
        
    try:
        # Build clients for DMs
        dm_clients = {}
        for cert in agent.certificates:
            client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
            dm_clients[cert.certificate_id] = (cert, client)
            
        # Build clients for Rooms
        room_store = RoomStore(state_path.parent / "rooms")
        room_memberships = {}
        for room_id in room_store.list_rooms():
            membership = room_store.load_membership(room_id)
            if membership:
                client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
                room_memberships[room_id] = (membership, client)
        
        # Load/Create ReadState for deduplication
        read_state_path = state_path.parent / "readstate.json"
        read_state = ReadState.load(read_state_path)
            
        turn_url = os.getenv("TURN_URL")
        turn_secret = os.getenv("TURN_SECRET")
        config = GatewayConfig(host=host, port=port, poll_interval=poll_interval, push=push, turn_url=turn_url, turn_secret=turn_secret)
        
        console.print(f"[green]Starting gateway on ws://{host}:{port}...[/green]")
        asyncio.run(run_gateway(agent, dm_clients, room_memberships, config, read_state))
    except Exception as exc:
        console.print(f"[red]Error starting gateway:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# proxion chat room topic / info  (E04)
# ---------------------------------------------------------------------------

@chat_room_app.command("topic")
def chat_room_topic(
    room_id: str = typer.Argument(help="Room ID."),
    text: str = typer.Argument(help="New topic text."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Set the topic for a room."""
    from .room import update_room_metadata
    from .room_store import RoomStore
    from .css_setup import build_dpop_client
    import os

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)

    try:
        room_store = RoomStore(state_path.parent / "rooms")
        membership = room_store.load_membership(room_id)
        if not membership:
            console.print(f"[red]Room not found:[/red] {room_id}")
            raise typer.Exit(1)
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        update_room_metadata(membership.room, client, topic=text)
        console.print(f"[green]Room topic updated:[/green] {text}")
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_room_app.command("info")
def chat_room_info(
    room_id: str = typer.Argument(help="Room ID."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Show detailed info about a room."""
    from .room import get_room_members
    from .room_store import RoomStore
    from .css_setup import build_dpop_client
    from rich.table import Table
    import os

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    try:
        room_store = RoomStore(state_path.parent / "rooms")
        membership = room_store.load_membership(room_id)
        if not membership:
            console.print(f"[red]Room not found:[/red] {room_id}")
            raise typer.Exit(1)
        room = membership.room

        member_count = 0
        if all([css_alice_url, css_client_id, css_client_secret]):
            try:
                client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
                member_count = len(get_room_members(room, client))
            except Exception:
                pass

        t = Table(title=f"Room: {room.name}", show_header=False, box=None)
        t.add_column("Field", style="bold cyan")
        t.add_column("Value")
        t.add_row("Room ID", room.room_id)
        t.add_row("Name", room.name)
        t.add_row("Owner", room.owner_webid)
        t.add_row("Topic", room.topic or "(none)")
        t.add_row("Description", room.description or "(none)")
        t.add_row("Created", room.created_at)
        t.add_row("Public", str(room.public))
        t.add_row("Read-only", str(room.read_only))
        t.add_row("Members", str(member_count) if member_count else "(unavailable)")
        console.print(t)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# proxion chat room leave  (R27-G)
# ---------------------------------------------------------------------------

@chat_room_app.command("leave")
def chat_room_leave(
    room_id: str = typer.Argument(help="Room ID to leave."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Leave a room and remove it from local membership store."""
    from .room_store import RoomStore

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    _load_state(state_path, pw)

    room_store = RoomStore(state_path.parent / "rooms")
    membership = room_store.load_membership(room_id)
    if membership is None:
        console.print(f"[yellow]Not a member of room:[/yellow] {room_id}")
        raise typer.Exit(1)
    room_store.delete_room(room_id)
    typer.echo(f"Left room {room_id}.")


# ---------------------------------------------------------------------------
# proxion chat dm delete  (R27-G)
# ---------------------------------------------------------------------------

@chat_dm_app.command("delete")
def chat_dm_delete(
    peer_cert_id: str = typer.Argument(help="Certificate ID of the DM thread to delete."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Remove a DM thread from local state."""
    from .persist import AgentState as _AgentState

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    # Remove the certificate matching peer_cert_id
    original = len(agent.certificates)
    agent.certificates = [
        c for c in agent.certificates
        if getattr(c, "certificate_id", None) != peer_cert_id
    ]
    removed = original - len(agent.certificates)
    if removed == 0:
        console.print(f"[yellow]No DM thread found:[/yellow] {peer_cert_id}")
        raise typer.Exit(1)
    agent.save(state_path, pw)
    typer.echo(f"Deleted DM thread {peer_cert_id}.")


# ---------------------------------------------------------------------------
# proxion chat export  (E01)
# ---------------------------------------------------------------------------

@chat_export_app.command("dm")
def chat_export_dm(
    peer_store_url: str = typer.Argument(help="Peer store URL (used to find the cert)."),
    fmt: str = typer.Option("json", "--format", "-f", help="Export format: json or markdown."),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Output file path."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Export a DM thread to JSON or Markdown."""
    from .export import export_thread_to_json, export_thread_to_markdown
    from .css_setup import build_dpop_client
    import os
    from datetime import datetime

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    agent = _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)

    if fmt not in ("json", "markdown"):
        console.print("[red]--format must be 'json' or 'markdown'[/red]")
        raise typer.Exit(1)

    # Find cert matching peer store URL
    cert = None
    for c in getattr(agent, "certificates", []):
        if peer_store_url in str(getattr(c, "subject", "")) or peer_store_url in str(getattr(c, "issuer", "")):
            cert = c
            break
    if cert is None and getattr(agent, "certificates", []):
        cert = agent.certificates[0]  # fallback: first cert
    if cert is None:
        console.print("[red]No certificate found for the given peer.[/red]")
        raise typer.Exit(1)

    ext = "md" if fmt == "markdown" else "json"
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = out or f"proxion-export-{cert.certificate_id[:8]}-{date_str}.{ext}"

    try:
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        if fmt == "json":
            count = export_thread_to_json(cert, client, output_path)
        else:
            count = export_thread_to_markdown(cert, client, output_path)
        console.print(f"[green]Exported {count} messages to:[/green] {output_path}")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@chat_export_app.command("room")
def chat_export_room(
    room_id: str = typer.Argument(help="Room ID."),
    fmt: str = typer.Option("json", "--format", "-f", help="Export format: json or markdown."),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Output file path."),
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
):
    """Export a room's message history to JSON or Markdown."""
    from .export import export_room_to_json, export_room_to_markdown
    from .room_store import RoomStore
    from .css_setup import build_dpop_client
    import os
    from datetime import datetime

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)
    _load_state(state_path, pw)

    css_alice_url = os.getenv("CSS_ALICE_URL")
    css_client_id = os.getenv("CSS_CLIENT_ID")
    css_client_secret = os.getenv("CSS_CLIENT_SECRET")

    if not all([css_alice_url, css_client_id, css_client_secret]):
        console.print("[red]Error:[/red] CSS_ALICE_URL, CSS_CLIENT_ID, CSS_CLIENT_SECRET required.")
        raise typer.Exit(1)

    if fmt not in ("json", "markdown"):
        console.print("[red]--format must be 'json' or 'markdown'[/red]")
        raise typer.Exit(1)

    ext = "md" if fmt == "markdown" else "json"
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = out or f"proxion-export-{room_id[:8]}-{date_str}.{ext}"

    try:
        room_store = RoomStore(state_path.parent / "rooms")
        membership = room_store.load_membership(room_id)
        if not membership:
            console.print(f"[red]Room not found:[/red] {room_id}")
            raise typer.Exit(1)
        client = build_dpop_client(css_alice_url, css_client_id, css_client_secret)
        if fmt == "json":
            count = export_room_to_json(membership, client, output_path)
        else:
            count = export_room_to_markdown(membership, client, output_path)
        console.print(f"[green]Exported {count} messages to:[/green] {output_path}")
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# proxion status  (E02)
# ---------------------------------------------------------------------------

@app.command("status")
def proxion_status(
    state: Optional[str] = _STATE_OPTION,
    passphrase: Optional[str] = _PASSPHRASE_OPTION,
    as_json: bool = typer.Option(False, "--json/--no-json", help="Output as JSON."),
):
    """Show Proxion component health status."""
    import socket
    import time
    import os
    from rich.table import Table

    state_path = _resolve_state(state)
    pw = _get_passphrase(passphrase)

    rows = []
    any_failed = False

    # Check 1: Agent state
    try:
        agent = _load_state(state_path, pw)
        webid = getattr(agent, "webid", None) or getattr(agent, "store_url", None) or "loaded"
        rows.append(("Agent state", "[green]OK[/green]", str(webid)))
    except Exception as exc:
        rows.append(("Agent state", "[red]FAIL[/red]", str(exc)))
        any_failed = True
        agent = None

    # Check 2: CSS Pod
    css_url = os.getenv("CSS_ALICE_URL")
    if css_url:
        try:
            import urllib.request
            t0 = time.monotonic()
            urllib.request.urlopen(css_url, timeout=5)
            ms = int((time.monotonic() - t0) * 1000)
            rows.append(("CSS Pod", "[green]OK[/green]", f"{css_url} ({ms}ms)"))
        except Exception as exc:
            rows.append(("CSS Pod", "[red]FAIL[/red]", str(exc)))
            any_failed = True
    else:
        rows.append(("CSS Pod", "[yellow]SKIP[/yellow]", "CSS_ALICE_URL not set"))

    # Check 3: Gateway port
    try:
        s = socket.socket()
        s.settimeout(5)
        s.connect(("127.0.0.1", 7474))
        s.close()
        rows.append(("Gateway", "[green]OK[/green]", "ws://127.0.0.1:7474"))
    except Exception:
        rows.append(("Gateway", "[yellow]DOWN[/yellow]", "port 7474 not open"))

    # Check 4: Cert summary
    if agent is not None:
        import time as _time
        now_ts = int(_time.time())
        certs = getattr(agent, "certificates", [])
        active = sum(1 for c in certs if getattr(c, "expires_at", now_ts + 1) > now_ts)
        expired = len(certs) - active
        rows.append(("Certs", "[green]OK[/green]" if certs else "[yellow]NONE[/yellow]",
                     f"{active} active, {expired} expired"))
    else:
        rows.append(("Certs", "[yellow]SKIP[/yellow]", "state not loaded"))

    # Check 5: Room count
    if agent is not None:
        try:
            from .room_store import RoomStore
            room_store = RoomStore(state_path.parent / "rooms")
            room_ids = room_store.list_rooms()
            rows.append(("Rooms", "[green]OK[/green]", f"{len(room_ids)} rooms"))
        except Exception:
            rows.append(("Rooms", "[yellow]SKIP[/yellow]", "room store unavailable"))

    if as_json:
        import json as _json
        # Strip Rich markup for JSON output
        import re
        def _strip(s: str) -> str:
            return re.sub(r"\[/?[^\]]+\]", "", s)
        payload = {
            "agent_ok": not any(
                "FAIL" in r[1] for r in rows if r[0] == "Agent state"
            ),
            "pod_ok": not any(
                "FAIL" in r[1] for r in rows if r[0] == "CSS Pod"
            ),
            "gateway_ok": any(
                "OK" in r[1] for r in rows if r[0] == "Gateway"
            ),
            "cert_count": next(
                (int(r[2].split()[0]) for r in rows if r[0] == "Certs" and r[2][0].isdigit()), 0
            ),
            "room_count": next(
                (int(r[2].split()[0]) for r in rows if r[0] == "Rooms" and r[2][0].isdigit()), 0
            ),
        }
        typer.echo(_json.dumps(payload))
        raise typer.Exit(1 if any_failed else 0)

    t = Table(title="Proxion Status", show_header=True, header_style="bold")
    t.add_column("Component", style="bold cyan")
    t.add_column("Status")
    t.add_column("Detail", style="dim")
    for name, status, detail in rows:
        t.add_row(name, status, detail)
    console.print(t)

    raise typer.Exit(1 if any_failed else 0)


def main():  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

