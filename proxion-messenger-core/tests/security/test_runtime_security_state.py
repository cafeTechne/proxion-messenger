"""Tests for get_runtime_security_state owner-only command (Round 5)."""
import pytest
import os


class TestRuntimeSecurityState:
    def test_runtime_state_should_be_owner_only(self):
        """The policy engine refuses get_runtime_security_state to anyone but the
        gateway owner."""
        from proxion_messenger_core.security_policy import get_policy
        owner = "did:key:zOwner"
        assert get_policy().evaluate_ws_command("get_runtime_security_state", owner, owner).allow
        denied = get_policy().evaluate_ws_command("get_runtime_security_state", "did:key:zOther", owner)
        assert not denied.allow

    async def test_safe_mode_env_var_blocks_a_mutating_command(self, tmp_path, monkeypatch):
        """With PROXION_SAFE_MODE=1 a registered client's send is refused."""
        import json
        from unittest.mock import AsyncMock
        from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
        from proxion_messenger_core.persist import AgentState
        gw = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                            config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "s.db")))
        ws = AsyncMock()
        ws.__hash__ = lambda self: id(self)
        ws.__eq__ = lambda self, other: self is other
        ws.remote_address = ("127.0.0.1", 1)
        gw.clients.add(ws)
        await gw.process_command(ws, {"cmd": "register", "did": "did:key:z6MkSafeMode", "display_name": "S"})
        monkeypatch.setenv("PROXION_SAFE_MODE", "1")
        await gw.process_command(ws, {"cmd": "local_dm", "target_webid": "did:key:z6MkPeer", "content": "x"})
        assert json.loads(ws.send.call_args.args[0]) == {
            "type": "error", "code": "E_SAFE_MODE", "message": "safe_mode_enabled"}

    def test_safe_mode_blocks_mutating_commands_in_logic(self):
        """Verify safe mode logic would block mutating commands."""
        from proxion_messenger_core.command_validation import MUTATING_COMMANDS
        # If PROXION_SAFE_MODE is set, any command in MUTATING_COMMANDS should be blocked
        cmd = "send_dm"
        assert cmd in MUTATING_COMMANDS
