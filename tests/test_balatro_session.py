import pytest
import threading
import time
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from skills.balatro.session import (
    BalatroSession,
    TickContext,
    start_session,
    stop_session,
    get_active_session,
)


@pytest.fixture
def mock_iris():
    iris = MagicMock()
    iris.vm = None
    iris.messages = []
    # Mock lock for prompt injection
    iris.lock = MagicMock()
    iris.lock.__enter__ = MagicMock(return_value=None)
    iris.lock.__exit__ = MagicMock(return_value=None)
    return iris


def test_tick_context_immutability():
    """Ensure TickContext properly deep-copies the raw state to prevent mutation leaks."""
    original_state = {
        "ante_num": 1,
        "hand": {"cards": [{"suit": "Spades", "value": "10"}], "count": 1},
        "jokers": {"cards": []}
    }
    
    context = TickContext.create(original_state)
    
    # Mutate the original state directly
    original_state["ante_num"] = 2
    original_state["hand"]["cards"][0]["suit"] = "Hearts"
    
    # Assert context state is unharmed
    assert context.raw_state["ante_num"] == 1
    assert context.raw_state["hand"]["cards"][0]["suit"] == "Spades"


@patch("skills.balatro.session.GameController")
def test_session_lifecycle_start_and_stop(mock_controller, mock_iris):
    """Test start and stop flow updates active_session globally."""
    stop_session()  # Clear any existing tests
    assert get_active_session() is None
    
    start_session(mock_iris, avatar=None)
    session1 = get_active_session()
    
    assert session1 is not None
    assert isinstance(session1, BalatroSession)
    assert not session1.stop_event.is_set()
    mock_iris.switch_mode.assert_called_with('balatro', avatar=None)
    
    stop_session()
    assert get_active_session() is None
    assert session1.stop_event.is_set()


@patch("skills.balatro.session.GameController")
def test_session_active_replacement(mock_controller, mock_iris):
    """Verify that starting a new session instantly terminates and replaces an old one."""
    stop_session()
    
    start_session(mock_iris, avatar=None)
    old_session = get_active_session()
    
    # Call start again
    start_session(mock_iris, avatar=None)
    new_session = get_active_session()
    
    assert new_session is not None
    assert old_session is not new_session
    assert old_session.stop_event.is_set(), "Old session must be stopped when replaced"
    assert not new_session.stop_event.is_set(), "New session should be active"
    
    stop_session()


@patch("skills.balatro.session.GameController")
def test_session_isolation(mock_controller, mock_iris):
    """Verify state data like memory and profiles are fully isolated between sessions."""
    s1 = BalatroSession(mock_iris, None)
    s2 = BalatroSession(mock_iris, None)
    
    s1.run_profile["deck"] = "CHECKERED"
    s1.action_memory.append("test action 1")
    
    assert s2.run_profile == {}, "Run profile leaked between sessions."
    assert len(s2.action_memory) == 0, "Action memory leaked between sessions."

