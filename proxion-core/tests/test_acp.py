"""Tests for proxion_messenger_core.acp."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from proxion_messenger_core.acp import detect_acl_mode, set_acp_policy, set_acl_auto


def _mock_pod_wac():
    client = MagicMock()
    client.head.return_value = {"Link": '<.acl>; rel="acl"'}
    return client


def _mock_pod_acp():
    client = MagicMock()
    client.head.return_value = {"Link": '<.acr>; rel="acr"'}
    return client


def test_detect_acl_mode_returns_wac_from_link_header():
    pod = _mock_pod_wac()
    assert detect_acl_mode(pod, "stash://messages/") == "wac"


def test_detect_acl_mode_returns_acp_from_acr_link():
    pod = _mock_pod_acp()
    assert detect_acl_mode(pod, "stash://messages/") == "acp"


def test_detect_acl_mode_falls_back_to_wac_on_error():
    pod = MagicMock()
    pod.head.side_effect = Exception("network error")
    assert detect_acl_mode(pod, "stash://messages/") == "wac"


def test_set_acp_policy_puts_correct_jsonld():
    pod = MagicMock()
    acr_uri = set_acp_policy(
        pod,
        "stash://messages/thread/c1/",
        owner_webid="alice@pod.example",
        subject_webid="bob@pod.example",
        subject_modes=["Read"],
    )

    assert acr_uri == "stash://messages/thread/c1/.acr"
    pod.put.assert_called_once()
    call_args = pod.put.call_args
    # Second positional arg is the body bytes
    body = call_args[0][1]
    doc = json.loads(body.decode("utf-8"))
    assert doc["@context"] == "http://www.w3.org/ns/solid/acp#"
    assert doc["policy"]["allOf"][0]["agent"] == "bob@pod.example"
    assert "Read" in doc["policy"]["allow"]


def test_set_acl_auto_calls_set_acl_for_wac():
    pod = _mock_pod_wac()
    set_acl_auto(pod, "stash://messages/", "alice", "bob", ["Read"])
    pod.set_acl.assert_called_once_with("stash://messages/", "alice", "bob", ["Read"])


def test_set_acl_auto_calls_set_acp_policy_for_acp():
    pod = _mock_pod_acp()
    acr = set_acl_auto(pod, "stash://messages/", "alice", "bob", ["Read"])
    assert acr.endswith(".acr")
    pod.put.assert_called_once()


def test_set_thread_read_acl_uses_set_acl_auto():
    """Test that set_thread_read_acl calls set_acl_auto instead of set_acl directly."""
    from proxion_messenger_core.solid_auth import set_thread_read_acl
    from unittest.mock import patch, MagicMock
    
    # Mock the inputs
    pod_client = _mock_pod_wac()
    cert = MagicMock()  # RelationshipCertificate
    cert.certificate_id = "c1"
    
    with patch("proxion_messenger_core.acp.set_acl_auto") as mock_set_acl_auto:
        mock_set_acl_auto.return_value = "stash://messages/thread/c1/.acl"
        with patch("proxion_messenger_core.messaging.thread_path", return_value="stash://messages/thread/c1/"):
            result = set_thread_read_acl(
                pod_client,
                cert,
                owner_webid="alice@pod.example",
                subject_webid="bob@pod.example",
            )
            
            # Verify set_acl_auto was called with correct params
            mock_set_acl_auto.assert_called_once_with(
                pod_client,
                "stash://messages/thread/c1/",
                "alice@pod.example",
                "bob@pod.example",
                subject_modes=["Read"],
            )
            assert result == "stash://messages/thread/c1/.acl"


def test_set_room_acl_acp_path_calls_set_acp_policy():
    """Test that set_room_acl detects ACP mode and calls set_acp_policy for each member."""
    from proxion_messenger_core.room import set_room_acl, RoomConfig
    from unittest.mock import patch, MagicMock
    
    # Create mock room config
    room = MagicMock(spec=RoomConfig)
    room.stash_root = "stash://rooms/r1/"
    room.owner_webid = "alice@pod.example"
    
    # Create mock client that indicates ACP mode
    owner_client = MagicMock()
    owner_client.head.return_value = {"Link": '<.acr>; rel="acr"'}
    
    member_webids = ["bob@pod.example", "charlie@pod.example"]
    
    with patch("proxion_messenger_core.acp.detect_acl_mode", return_value="acp"):
        with patch("proxion_messenger_core.acp.set_acp_policy") as mock_set_acp_policy:
            mock_set_acp_policy.return_value = "stash://rooms/r1/.acr"
            result = set_room_acl(room, owner_client, "alice@pod.example", member_webids)
            
            # Verify set_acp_policy was called for each member
            assert mock_set_acp_policy.call_count == 2
            calls = mock_set_acp_policy.call_args_list
            assert calls[0][0][2] == "alice@pod.example"  # owner
            assert calls[0][0][3] == "bob@pod.example"    # first member
            assert calls[1][0][3] == "charlie@pod.example"  # second member
            assert result == "stash://rooms/r1/.acr"

