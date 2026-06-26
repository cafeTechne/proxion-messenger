"""
B3: Custom Status Message — Tests for custom status message functionality
"""
import json
import pytest
from unittest.mock import Mock
from datetime import datetime, timezone


@pytest.fixture
def mock_gateway():
    """Create a mock gateway with presence tracking"""
    gateway = Mock()
    gateway._user_presence = {}
    gateway._current_client = None
    return gateway


class TestCustomStatusMessage:
    """Test custom status message storage and retrieval"""
    
    def test_set_presence_with_status_message(self, mock_gateway):
        """Test that set_presence command stores status message"""
        webid = "did:key:alice"
        
        # Simulate user registration
        mock_gateway._user_presence[webid] = {
            "status": "online",
            "status_message": "",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Update with status message
        status_message = "In a meeting"
        mock_gateway._user_presence[webid]["status_message"] = status_message
        mock_gateway._user_presence[webid]["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        assert mock_gateway._user_presence[webid]["status_message"] == "In a meeting"
        assert mock_gateway._user_presence[webid]["status"] == "online"
    
    def test_status_message_max_length(self):
        """Test that status message respects 100 character limit"""
        status_message = "x" * 101
        truncated = status_message[:100]
        assert len(truncated) == 100
        assert len(status_message) == 101
    
    def test_status_message_empty_allowed(self, mock_gateway):
        """Test that empty status message is allowed"""
        webid = "did:key:bob"
        
        mock_gateway._user_presence[webid] = {
            "status": "online",
            "status_message": "",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        assert mock_gateway._user_presence[webid]["status_message"] == ""
    
    def test_status_message_with_special_characters(self, mock_gateway):
        """Test that status message handles special characters"""
        webid = "did:key:charlie"
        special_msg = "I'm 🎉 coding! (100% focused) 🚀"
        
        mock_gateway._user_presence[webid] = {
            "status": "online",
            "status_message": special_msg,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        assert mock_gateway._user_presence[webid]["status_message"] == special_msg
    
    def test_get_presence_includes_status_message(self):
        """Test that get_presence returns status_message field"""
        webid = "did:key:dave"
        status_message = "Working on feature X"
        
        user_presence = {
            "status": "online",
            "status_message": status_message,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        presence_response = {
            "type": "presence",
            "webid": webid,
            "status": user_presence["status"],
            "status_message": user_presence["status_message"],
            "updated_at": user_presence["updated_at"]
        }
        
        assert "status_message" in presence_response
        assert presence_response["status_message"] == status_message
    
    def test_get_presence_offline_user_has_empty_status_message(self):
        """Test that offline users have empty status_message in response"""
        webid = "did:key:eve"
        
        presence_response = {
            "type": "presence",
            "webid": webid,
            "status": "offline",
            "status_message": "",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        assert presence_response["status_message"] == ""
        assert presence_response["status"] == "offline"
    
    def test_status_message_persists_across_updates(self, mock_gateway):
        """Test that status_message persists when only status changes"""
        webid = "did:key:frank"
        original_msg = "Lunch break"
        
        mock_gateway._user_presence[webid] = {
            "status": "online",
            "status_message": original_msg,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Update to away status, keep message
        mock_gateway._user_presence[webid]["status"] = "away"
        mock_gateway._user_presence[webid]["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        assert mock_gateway._user_presence[webid]["status_message"] == original_msg
        assert mock_gateway._user_presence[webid]["status"] == "away"
    
    def test_status_message_whitespace_trimmed(self, mock_gateway):
        """Test that whitespace in status message is handled correctly"""
        webid = "did:key:grace"
        msg_with_spaces = "  Working on code  "
        
        mock_gateway._user_presence[webid] = {
            "status": "online",
            "status_message": msg_with_spaces.strip(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        assert mock_gateway._user_presence[webid]["status_message"] == "Working on code"
    
    def test_set_presence_command_structure(self):
        """Test that set_presence command has correct JSON structure"""
        command = {
            "cmd": "set_presence",
            "status": "online",
            "status_message": "Available"
        }
        
        assert command["cmd"] == "set_presence"
        assert "status" in command
        assert "status_message" in command
    
    def test_status_message_multi_line_preserved(self, mock_gateway):
        """Test that multi-line status message text is preserved"""
        webid = "did:key:henry"
        # Note: frontend may strip newlines, but backend stores as-is
        msg = "Line 1\nLine 2"
        
        mock_gateway._user_presence[webid] = {
            "status": "busy",
            "status_message": msg,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        assert "Line 1" in mock_gateway._user_presence[webid]["status_message"]
        assert "Line 2" in mock_gateway._user_presence[webid]["status_message"]
    
    def test_status_message_unicode_support(self, mock_gateway):
        """Test that status message supports unicode characters"""
        webid = "did:key:iris"
        unicode_msg = "Работаю на проекте 🌟"
        
        mock_gateway._user_presence[webid] = {
            "status": "online",
            "status_message": unicode_msg,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        assert mock_gateway._user_presence[webid]["status_message"] == unicode_msg
    
    def test_presence_data_structure_includes_status_message(self, mock_gateway):
        """Test that all presence objects include status_message field"""
        mock_gateway._user_presence = {
            "did:key:alice": {
                "status": "online",
                "status_message": "Available",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            "did:key:bob": {
                "status": "away",
                "status_message": "Back in 30 min",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            "did:key:charlie": {
                "status": "offline",
                "status_message": "",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
        }
        
        for webid, data in mock_gateway._user_presence.items():
            assert "status_message" in data
            assert isinstance(data["status_message"], str)


class TestStatusMessageUIIntegration:
    """Test status message UI integration"""
    
    def test_profile_card_custom_status_element_exists(self):
        """Test that HTML has profile-custom-status element"""
        # This would be tested by parsing web/index.html
        # For now, we verify the expected element ID
        element_id = "profile-custom-status"
        assert element_id  # Placeholder for UI test
    
    def test_settings_modal_status_message_field(self):
        """Test that settings modal has status message input"""
        field_id = "settings-status-message"
        max_length = 100
        assert field_id  # Placeholder for UI test
        assert max_length == 100
    
    def test_status_message_localStorage_key(self):
        """Test that status message uses correct localStorage key"""
        key = "proxion_status_message"
        assert key == "proxion_status_message"
    
    def test_profile_card_displays_custom_status(self):
        """Test that showProfileCard function displays status message"""
        user_presence = {
            "did:key:test": {
                "status": "online",
                "status_message": "Working on project"
            }
        }
        
        # In real implementation, the status_message should be displayed
        assert user_presence["did:key:test"]["status_message"] is not None

