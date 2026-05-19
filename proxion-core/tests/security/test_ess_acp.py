"""Tests for ACP v3 Turtle generation and ESS-aware set_acl_auto."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ---------------------------------------------------------------------------
# set_acp_v3_policy
# ---------------------------------------------------------------------------

class TestSetAcpV3Policy:
    def _mock_client(self):
        c = MagicMock()
        c.put = MagicMock()
        return c

    def test_generates_turtle_not_json_ld(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        set_acp_v3_policy(client, "https://pod/resource", "https://alice.example/#me", "https://bob.example/#me")
        content_type = client.put.call_args[1]["content_type"]
        assert content_type == "text/turtle"

    def test_turtle_contains_acp_prefix(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        set_acp_v3_policy(client, "https://pod/resource", "https://alice/#me", "https://bob/#me")
        body = client.put.call_args[0][1].decode()
        assert "acp:AccessControlResource" in body

    def test_turtle_contains_subject_webid(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        set_acp_v3_policy(client, "https://pod/res", "https://owner/#me", "https://member/#me")
        body = client.put.call_args[0][1].decode()
        assert "https://member/#me" in body

    def test_turtle_contains_owner_webid(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        set_acp_v3_policy(client, "https://pod/res", "https://owner/#me", "https://member/#me")
        body = client.put.call_args[0][1].decode()
        assert "https://owner/#me" in body

    def test_default_mode_is_read(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        set_acp_v3_policy(client, "https://pod/res", "https://o/#me", "https://s/#me")
        body = client.put.call_args[0][1].decode()
        assert "acl:Read" in body

    def test_custom_modes_included(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        set_acp_v3_policy(client, "https://pod/res", "https://o/#me", "https://s/#me", ["Read", "Write"])
        body = client.put.call_args[0][1].decode()
        assert "acl:Read" in body
        assert "acl:Write" in body

    def test_owner_gets_control_access(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        set_acp_v3_policy(client, "https://pod/res", "https://o/#me", "https://s/#me")
        body = client.put.call_args[0][1].decode()
        assert "acl:Control" in body

    def test_returns_acr_url(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        result = set_acp_v3_policy(client, "https://pod/res", "https://o/#me", "https://s/#me")
        assert result == "https://pod/res.acr"

    def test_unsafe_owner_webid_raises(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        with pytest.raises(ValueError):
            set_acp_v3_policy(client, "https://pod/res", 'bad"id', "https://s/#me")

    def test_unsafe_subject_webid_raises(self):
        from proxion_messenger_core.acp import set_acp_v3_policy
        client = self._mock_client()
        with pytest.raises(ValueError):
            set_acp_v3_policy(client, "https://pod/res", "https://o/#me", 'bad\nid')


# ---------------------------------------------------------------------------
# detect_pod_type
# ---------------------------------------------------------------------------

class TestDetectPodType:
    @pytest.mark.asyncio
    async def test_ess_from_inrupt_server_header(self):
        from proxion_messenger_core.acp import detect_pod_type
        fake_headers = {"server": "Inrupt ESS 2.1", "x-powered-by": "", "link": "", "www-authenticate": ""}
        with patch("proxion_messenger_core.network.async_safe_head", AsyncMock(return_value=fake_headers)):
            result = await detect_pod_type("https://pod.inrupt.com")
        assert result == "ess"

    @pytest.mark.asyncio
    async def test_ess_from_www_authenticate_header(self):
        from proxion_messenger_core.acp import detect_pod_type
        fake_headers = {
            "server": "", "x-powered-by": "",
            "link": "", "www-authenticate": 'Bearer realm="inrupt"',
        }
        with patch("proxion_messenger_core.network.async_safe_head", AsyncMock(return_value=fake_headers)):
            result = await detect_pod_type("https://pod.example")
        assert result == "ess"

    @pytest.mark.asyncio
    async def test_css_from_server_header(self):
        from proxion_messenger_core.acp import detect_pod_type
        fake_headers = {
            "server": "Community Solid Server/7.1.0", "x-powered-by": "",
            "link": "", "www-authenticate": "",
        }
        with patch("proxion_messenger_core.network.async_safe_head", AsyncMock(return_value=fake_headers)):
            result = await detect_pod_type("https://localhost:3001")
        assert result == "css"

    @pytest.mark.asyncio
    async def test_ess_from_acr_link_without_css_signal(self):
        from proxion_messenger_core.acp import detect_pod_type
        fake_headers = {
            "server": "nginx", "x-powered-by": "",
            "link": '<https://pod/res.acr>; rel="acr"',
            "www-authenticate": "",
        }
        with patch("proxion_messenger_core.network.async_safe_head", AsyncMock(return_value=fake_headers)):
            result = await detect_pod_type("https://pod.example")
        assert result == "ess"

    @pytest.mark.asyncio
    async def test_unknown_on_network_error(self):
        from proxion_messenger_core.acp import detect_pod_type
        with patch("proxion_messenger_core.network.async_safe_head", AsyncMock(return_value=None)):
            result = await detect_pod_type("https://unreachable.example")
        assert result == "unknown"


# ---------------------------------------------------------------------------
# set_acl_auto — pod_type dispatch
# ---------------------------------------------------------------------------

class TestSetAclAutoPodType:
    def _mock_client_with_head(self, link_rel):
        c = MagicMock()
        c.head = MagicMock(return_value={"Link": f'<res.acr>; {link_rel}'})
        c.put = MagicMock()
        c.set_acl = MagicMock()
        return c

    def test_wac_pod_uses_set_acl(self):
        from proxion_messenger_core.acp import set_acl_auto
        client = MagicMock()
        client.head = MagicMock(return_value={"Link": '<res.acl>; rel="acl"'})
        client.set_acl = MagicMock()
        set_acl_auto(client, "https://pod/res", "https://o/#me", "https://s/#me")
        client.set_acl.assert_called_once()

    def test_acp_css_pod_uses_json_ld(self):
        from proxion_messenger_core.acp import set_acl_auto
        client = MagicMock()
        client.head = MagicMock(return_value={"Link": '<res.acr>; rel="acr"'})
        client.put = MagicMock()
        set_acl_auto(client, "https://pod/res", "https://o/#me", "https://s/#me", pod_type="css")
        # JSON-LD ACP uses application/ld+json
        content_type = client.put.call_args[1]["content_type"]
        assert content_type == "application/ld+json"

    def test_acp_ess_pod_uses_turtle(self):
        from proxion_messenger_core.acp import set_acl_auto
        client = MagicMock()
        client.head = MagicMock(return_value={"Link": '<res.acr>; rel="acr"'})
        client.put = MagicMock()
        set_acl_auto(client, "https://pod/res", "https://o/#me", "https://s/#me", pod_type="ess")
        content_type = client.put.call_args[1]["content_type"]
        assert content_type == "text/turtle"
