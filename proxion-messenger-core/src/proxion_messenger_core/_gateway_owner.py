"""OwnerMixin: who owns this gateway, kept across restarts, plus the one-time
claim that gives a freshly started public gateway its first owner.

Mixin class: add to ProxionGateway's MRO so all methods share `self`.
Requires on self: _store, _client_webids, _session_meta, _session_device_did,
                  _owner_account_dids, _proven_sockets, config, agent,
                  _auth_enforced(), _owner_by_did_or_local(), _auth_fail_key().

An owner is an account identity (the did a session registers as). The
desktop browser on loopback and anything proven to be the gateway's own did
are owners the way they always were; each such identity is now recorded in the
store, so a later session that proves it (auth challenge, or a linked device's
delegation cert) is the owner too, after a restart and over a tunnel.

A gateway that requires auth by configuration (a public bind, or
PROXION_REQUIRE_AUTH=1 behind a reverse proxy) has no loopback owner. Until it
has an owner it keeps a one-time claim code and logs a link carrying it. The
code is only ever written to the gateway log; a session that proved its did
presents it once to become the owner, and from then on codes stop working.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import time

logger = logging.getLogger("proxion_messenger_core.gateway")


class OwnerMixin:

    _OWNER_CLAIM_TTL = 15 * 60          # seconds a claim code stays valid
    _OWNER_CLAIM_FAIL_WINDOW = 600.0    # seconds a failed attempt counts
    _OWNER_CLAIM_FAIL_LIMIT = 5         # failed attempts per source per window

    # ---- persisted owner set --------------------------------------------------

    def _load_owner_dids(self) -> None:
        owners = getattr(self, "_owner_account_dids", None)
        if owners is None:
            owners = self._owner_account_dids = set()
        if not self._store:
            return
        try:
            owners.update(d["did"] for d in self._store.list_owner_devices())
        except Exception:
            logger.warning("owner devices could not be loaded", exc_info=True)

    def _remember_owner(self, did: str, via: str) -> bool:
        """Record *did* as an owner. Only did:key identities are kept (a pod
        WebID register is never proven), and never the gateway's own did, which
        is the owner by definition. Returns whether it was newly added."""
        if not isinstance(did, str) or not did.startswith("did:key:"):
            return False
        try:
            if did == self._own_gateway_did():
                return False
        except Exception:
            pass
        owners = getattr(self, "_owner_account_dids", None)
        if owners is None:
            owners = self._owner_account_dids = set()
        if did in owners:
            return False
        owners.add(did)
        if self._store:
            try:
                self._store.add_owner_device(did, via)
            except Exception:
                logger.warning("owner device could not be saved", exc_info=True)
        # The gateway has an owner now: any outstanding claim code is dead.
        self._owner_claim = None
        logger.info("gateway owner added (%s): %s...", via, did[:24])
        return True

    def _forget_owner(self, did: str) -> bool:
        owners = getattr(self, "_owner_account_dids", None) or set()
        found = did in owners
        owners.discard(did)
        if self._store:
            try:
                found = self._store.remove_owner_device(did) or found
            except Exception:
                logger.warning("owner device could not be removed", exc_info=True)
        return found

    def _is_persisted_owner(self, websocket) -> bool:
        """Whether *websocket* registered as a recorded owner AND proved it.
        An unproven self-claim (auth off) never matches the recorded set."""
        if websocket not in getattr(self, "_proven_sockets", ()):
            return False
        caller = self._client_webids.get(websocket, "")
        return bool(caller) and caller in (getattr(self, "_owner_account_dids", None) or ())

    # ---- claim ----------------------------------------------------------------

    def _auth_enforced_by_config(self) -> bool:
        """_auth_enforced without the live-tunnel override: whether this
        gateway requires proof of identity because of how it was deployed."""
        _env = os.environ.get("PROXION_REQUIRE_AUTH", "")
        if _env == "1":
            return True
        if _env == "0":
            return False
        _host = (getattr(self, "config", None) and self.config.host) or ""
        return _host not in ("127.0.0.1", "localhost", "::1")

    def _owner_claim_open(self) -> bool:
        """A gateway can be claimed only while it requires auth by
        configuration and has no recorded owner."""
        return (self._auth_enforced_by_config()
                and not (getattr(self, "_owner_account_dids", None) or set()))

    def _owner_claim_code(self) -> str:
        """The live claim code, minted (and logged) when there is none or the
        last one expired. Empty when the gateway cannot be claimed."""
        if not self._owner_claim_open():
            self._owner_claim = None
            return ""
        claim = getattr(self, "_owner_claim", None)
        now = time.time()
        if claim and claim["expires_at"] > now:
            return claim["code"]
        code = secrets.token_urlsafe(24)
        self._owner_claim = {"code": code, "expires_at": now + self._OWNER_CLAIM_TTL}
        logger.warning(
            "This gateway has no owner yet. Open %s to claim it "
            "(single use, valid %d minutes; a new link is logged when it expires).",
            self._owner_claim_url_for(code), self._OWNER_CLAIM_TTL // 60,
        )
        return code

    def _owner_claim_url_for(self, code: str) -> str:
        cfg = getattr(self, "config", None)
        base = (getattr(cfg, "http_public_url", None) or "").rstrip("/")
        if not base:
            scheme = "https" if (getattr(cfg, "ssl_certfile", None)
                                 and getattr(cfg, "ssl_keyfile", None)) else "http"
            host = (getattr(cfg, "host", "") or "").strip()
            if host in ("", "0.0.0.0", "::"):
                host = "<server-address>"
            elif ":" in host:
                host = f"[{host}]"
            port = getattr(cfg, "http_port", None)
            base = f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"
        return f"{base}/#claim={code}"

    def owner_claim_url(self) -> str:
        """The ready-made claim link for the startup banner, or "" when the
        gateway already has an owner or does not need one claimed."""
        code = self._owner_claim_code()
        return self._owner_claim_url_for(code) if code else ""

    async def _owner_claim_loop(self) -> None:
        """Keep a fresh code (and its log line) while the gateway is unclaimed."""
        import asyncio
        while True:
            self._owner_claim_code()
            await asyncio.sleep(30)

    def _owner_claim_locked(self, websocket, now: float) -> bool:
        fails = self.__dict__.setdefault("_owner_claim_fails", {})
        for key in [k for k, v in fails.items()
                    if now - v["first_at"] > self._OWNER_CLAIM_FAIL_WINDOW]:
            fails.pop(key, None)
        entry = fails.get(self._auth_fail_key(websocket))
        return bool(entry) and entry["count"] >= self._OWNER_CLAIM_FAIL_LIMIT

    def _owner_claim_failed(self, websocket, now: float) -> None:
        fails = self.__dict__.setdefault("_owner_claim_fails", {})
        key = self._auth_fail_key(websocket)
        entry = fails.setdefault(key, {"count": 0, "first_at": now})
        entry["count"] += 1

    def _gateway_cert_for(self, websocket) -> "dict | None":
        """The gateway delegation cert for this session's signing key."""
        signing = (self._session_device_did.get(websocket)
                   or self._client_webids.get(websocket, ""))
        if not str(signing).startswith("did:key:"):
            return None
        try:
            from .device_cert import issue_device_cert
            return issue_device_cert(self.agent.identity_key, signing, ttl_days=30)
        except Exception:
            logger.debug("gateway delegation cert issue skipped", exc_info=True)
            return None

    async def _handle_claim_gateway(self, websocket, data: dict) -> None:
        """Make the caller's proven identity an owner, given the claim code from
        the gateway log (only while unclaimed) or PROXION_ADMIN_API_TOKEN."""
        async def _fail(reason: str) -> None:
            await websocket.send(json.dumps({"type": "claim_failed", "reason": reason}))

        caller = self._client_webids.get(websocket, "")
        # Only an identity this socket proved (auth challenge, plus a verified
        # delegation cert for a linked device) can be bound, never a did from
        # the payload, and never an unproven self-claim.
        if (not caller or not caller.startswith("did:key:") or not self._auth_enforced()
                or websocket not in getattr(self, "_proven_sockets", ())):
            await _fail("auth_required")
            return
        now = time.time()
        if self._owner_claim_locked(websocket, now):
            await _fail("rate_limited")
            return
        presented = data.get("code", "")
        if not isinstance(presented, str):
            presented = ""
        presented = presented.strip()
        ok, via = False, ""
        # Compare against both secrets every time, so the reply time does not
        # say which one (if any) is configured or live.
        admin = os.environ.get("PROXION_ADMIN_API_TOKEN", "")
        claim = getattr(self, "_owner_claim", None) if self._owner_claim_open() else None
        live = claim["code"] if claim and claim["expires_at"] > now else ""
        p = presented.encode()
        admin_ok = hmac.compare_digest(p, (admin or "\x00" * 32).encode()) and bool(admin)
        code_ok = hmac.compare_digest(p, (live or "\x01" * 32).encode()) and bool(live)
        if presented and admin_ok:
            ok, via = True, "token"
        elif presented and code_ok:
            ok, via = True, "claim"
        if not ok:
            self._owner_claim_failed(websocket, now)
            if self._store:
                try:
                    self._store.save_security_event(
                        "owner_claim_failed", "warning", webid=caller,
                        ip=(self._session_meta.get(websocket) or {}).get("ip_addr") or None,
                        details="claim code or token rejected")
                except Exception:
                    pass
            await _fail("already_claimed" if not self._owner_claim_open() and not admin
                        else "invalid_code")
            return
        # No await between the check above and this commit: a second claimer
        # with the same code finds it already consumed.
        self._owner_claim = None
        self._remember_owner(caller, via)
        if self._store:
            try:
                self._store.save_security_event(
                    "owner_claimed", "warning", webid=caller,
                    ip=(self._session_meta.get(websocket) or {}).get("ip_addr") or None,
                    details=f"gateway owner claimed via {via}")
            except Exception:
                pass
        out = {"type": "gateway_claimed", "webid": caller}
        cert = self._gateway_cert_for(websocket)
        if cert:
            out["gateway_delegation_cert"] = cert
        await websocket.send(json.dumps(out))

    # ---- owner device management ---------------------------------------------

    async def _handle_list_owner_devices(self, websocket, data: dict) -> None:
        if not self._owner_by_did_or_local(websocket):
            await websocket.send(json.dumps({
                "type": "error", "code": "E_FORBIDDEN", "message": "gateway_owner_only"}))
            return
        caller = self._client_webids.get(websocket, "")
        rows = self._store.list_owner_devices() if self._store else [
            {"did": d, "via": "", "added_at": None}
            for d in sorted(getattr(self, "_owner_account_dids", None) or ())]
        devices = []
        for r in rows:
            name = ""
            if self._store:
                try:
                    name = self._store.get_display_name(r["did"]) or ""
                except Exception:
                    name = ""
            devices.append({"did": r["did"], "display_name": name, "via": r.get("via", ""),
                            "added_at": r.get("added_at"), "self": r["did"] == caller})
        await websocket.send(json.dumps({"type": "owner_devices", "devices": devices}))

    async def _handle_remove_owner_device(self, websocket, data: dict) -> None:
        if not self._owner_by_did_or_local(websocket):
            await websocket.send(json.dumps({
                "type": "error", "code": "E_FORBIDDEN", "message": "gateway_owner_only"}))
            return
        did = data.get("did", "")
        caller = self._client_webids.get(websocket, "")
        # Removing yourself (perhaps the last owner) needs an explicit confirm.
        if did == caller and data.get("confirm") is not True:
            await websocket.send(json.dumps({
                "type": "error", "code": "E_CONFIRM", "message": "confirm_required"}))
            return
        removed = self._forget_owner(did)
        if removed and self._store:
            try:
                self._store.save_security_event(
                    "owner_removed", "warning", webid=caller,
                    details=f"removed owner {did[:40]}")
            except Exception:
                pass
        await websocket.send(json.dumps({
            "type": "owner_device_removed", "did": did, "removed": removed}))
        # The last owner of a public gateway is gone: open a new claim.
        self._owner_claim_code()
        if self._owner_by_did_or_local(websocket):
            await self._handle_list_owner_devices(websocket, {})
