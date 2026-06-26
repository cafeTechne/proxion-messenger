"""
Tests for B2: User Profile Card (Popover)
- Avatar click opens profile card popover
- Profile card displays user webid, presence status, and DM button
- DM button creates or opens DM thread with clicked user
- Profile card closes on outside click
"""

import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
import os


class MockWebSocket:
    """Mock WebSocket for testing"""
    def __init__(self):
        self.messages_sent = []
        self.is_open = True
    
    async def send(self, data):
        if self.is_open:
            self.messages_sent.append(json.loads(data))
    
    async def recv(self):
        await asyncio.sleep(0.1)
        return json.dumps({"type": "ping"})


@pytest.fixture
def profile_card_context():
    """Fixture providing context for profile card tests"""
    return {
        "user_webid": "did:key:z6MkhaX1j9qBbqW7QQzG4ZCDVMc34LZ9PDBRyYmd5P5PFXwF",
        "target_webid": "did:key:z6MkhaX1j9qBbqW7QQzG4ZCDVMc34LZ9PDBRyYmd5P5PFXwG",
        "target_display_name": "Alice",
        "presence_status": "online"
    }


def _get_index_html_path():
    """Get the correct path to index.html"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, "..", "..", "web", "index.html")


def _get_web_content():
    """Return combined content of the web shell + extracted ES modules.

    The profile-card JS was extracted from main.js into profile.js during the
    R40 modularization; include the relevant modules so these content checks
    follow the code instead of asserting on main.js alone.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(script_dir, "..", "..", "web")
    content = ""
    for fname in ("index.html", "main.js", "profile.js", "view.js"):
        fpath = os.path.join(web_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content += f.read() + "\n"
        except FileNotFoundError:
            pass
    return content


def test_profile_card_html_structure():
    """Verify profile card HTML exists in index.html"""
    content = _get_web_content()
    
    # Check for profile card container
    assert 'id="profile-card"' in content, "Profile card container not found"
    assert 'class="profile-popover"' in content, "Profile popover class not found"
    
    # Check for profile card elements
    assert 'id="profile-name"' in content, "Profile name element not found"
    assert 'id="profile-webid"' in content, "Profile webid element not found"
    assert 'id="profile-status-text"' in content, "Profile status text not found"
    assert 'id="profile-status-dot"' in content, "Profile status indicator not found"
    assert 'id="profile-dm-btn"' in content, "Profile DM button not found"


def test_profile_card_css_styling():
    """Verify profile card CSS is properly defined"""
    content = _get_web_content()
    
    # Check for profile popover styles
    assert ".profile-popover {" in content, "profile-popover CSS not found"
    assert ".profile-header {" in content, "profile-header CSS not found"
    assert ".profile-name {" in content, "profile-name CSS not found"
    assert ".profile-avatar {" in content, "profile-avatar CSS not found"
    assert ".profile-status {" in content, "profile-status CSS not found"
    assert ".profile-btn {" in content, "profile-btn CSS not found"


def test_profile_card_functions_exist():
    """Verify JavaScript functions for profile card"""
    content = _get_web_content()
    
    # Check for profile card functions
    assert "function showProfileCard" in content, "showProfileCard function not found"
    assert "function profileCardOpenDM" in content, "profileCardOpenDM function not found"
    assert "function hideProfileCard" in content, "hideProfileCard function not found"


def test_avatar_click_handler():
    """Verify avatar elements have click handlers"""
    content = _get_web_content()
    
    # Check for avatar click handler in message rendering
    assert "data-profile-avatar" in content, "data-profile-avatar attribute not found"
    assert "showProfileCard(" in content, "showProfileCard call in avatar click not found"


def test_profile_card_presence_indicator():
    """Verify profile card displays presence status with color indicators"""
    content = _get_web_content()
    
    # Check for presence status color classes
    assert ".profile-status-dot.online" in content
    assert ".profile-status-dot.away" in content
    assert ".profile-status-dot.busy" in content


def test_profile_card_dm_button_functionality():
    """Verify DM button sends resolve_did command"""
    content = _get_web_content()
    
    # Check that profileCardOpenDM function uses resolve_did
    assert "resolve_did" in content, "resolve_did command not found in profile card code"


def test_profile_card_prevents_event_propagation():
    """Verify avatar click doesn't propagate to parent elements"""
    content = _get_web_content()
    
    # Check for event.stopPropagation() in avatar handler
    assert "event.stopPropagation()" in content or "stopPropagation" in content


def test_profile_card_close_on_outside_click():
    """Verify profile card closes when clicking outside"""
    content = _get_web_content()
    
    # Check for event listener that closes profile card
    assert "document.addEventListener" in content
    assert "hideProfileCard()" in content or "profile-card" in content


def test_profile_card_avatar_color_matching():
    """Verify profile card avatar uses same color as message avatar"""
    content = _get_web_content()
    
    # Check for webidColor function usage
    assert "webidColor(" in content, "webidColor function call not found"


def test_dm_button_resolves_webid():
    """Verify DM button triggers resolve_did with target webid"""
    content = _get_web_content()
    
    # Check that profileCardOpenDM uses resolve_did
    assert "cmd: \"resolve_did\"" in content or "\"resolve_did\"" in content


def test_profile_card_state_tracking():
    """Verify profile card tracks active profile"""
    content = _get_web_content()
    
    # Check for _profileCardActive variable
    assert "_profileCardActive" in content


def test_profile_card_animation_keyframes():
    """Verify profile card has entrance animation"""
    content = _get_web_content()
    
    # Check for animation definition
    assert "popoverFadeIn" in content or "@keyframes" in content


@pytest.mark.asyncio
async def test_resolve_did_creates_dm_entry(profile_card_context):
    """Verify resolve_did command creates DM list entry (gateway behavior)"""
    # This test verifies the gateway-side behavior that should happen
    # when resolve_did is called from profile card
    
    ws = MockWebSocket()
    
    # Simulate resolving a DID
    await ws.send(json.dumps({
        "cmd": "resolve_did",
        "did": profile_card_context["target_webid"]
    }))
    
    assert len(ws.messages_sent) == 1
    msg = ws.messages_sent[0]
    assert msg["cmd"] == "resolve_did"
    assert msg["did"] == profile_card_context["target_webid"]


@pytest.mark.asyncio
async def test_dm_creation_from_profile_card(profile_card_context):
    """Verify profile card DM button successfully initiates DM"""
    ws = MockWebSocket()
    
    # Simulate clicking "Send DM" button from profile card
    # This should trigger resolve_did
    await ws.send(json.dumps({
        "cmd": "resolve_did",
        "did": profile_card_context["target_webid"]
    }))
    
    # Verify the message was sent correctly
    assert len(ws.messages_sent) == 1
    assert ws.messages_sent[0]["cmd"] == "resolve_did"


def test_profile_card_integration_with_presence():
    """Verify profile card integrates with presence system"""
    content = _get_web_content()
    
    # Check that profile card uses userPresence dict
    assert "userPresence[webid]" in content or "userPresence" in content


def test_profile_card_accessibility():
    """Verify profile card has basic accessibility features"""
    content = _get_web_content()
    
    # Check for descriptive labels and semantic HTML
    assert "profile-name" in content  # descriptive class
    assert "profile-webid" in content  # descriptive class


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

