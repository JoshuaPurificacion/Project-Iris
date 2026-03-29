"""
test_balatro.py

Covers:
  - Lifecycle: start/stop/restart safety (no double-start, clean teardown)
  - Action tag parsing: case variants, bracketed, with/without arg, no tag
  - State hash: identical state → same hash; changed state → different hash
"""

import hashlib
import json
import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class DummyVM:
    def __init__(self):
        self.speech_queue = queue.Queue()
        self.is_speaking = threading.Event()
        self.abort_flag = threading.Event()

    def speak(self, text, avatar=None):
        print(f"[DummyTTS] {text}")

    def interrupt_playback(self, avatar=None, reason=None):
        self.abort_flag.set()


# ---------------------------------------------------------------------------
# Import the module under test AFTER stubs are ready
# ---------------------------------------------------------------------------

from skills.balatro_skill import (
    _parse_action,
    _state_hash,
    start_balatro,
    stop_balatro,
)
import skills.balatro_skill as _bmod


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestBalatroLifecycle(unittest.TestCase):
    def setUp(self):
        # Ensure clean state before each test
        stop_balatro()
        time.sleep(0.05)

    def _make_iris(self):
        """Minimal Iris stub that won't block on LLM."""
        iris = MagicMock()
        iris.vm = DummyVM()
        iris.chat.return_value = "[play_hand]"
        return iris

    @patch("skills.balatro_skill.GameController", autospec=True)
    def test_start_sets_running_flag(self, MockGC):
        """start_balatro() sets _balatro_running and creates a controller."""
        MockGC.return_value.refresh_state.return_value = False  # stall loop
        iris = self._make_iris()
        start_balatro(iris, avatar=None)
        time.sleep(0.05)
        with _bmod._balatro_lock:
            self.assertTrue(_bmod._balatro_running)
            self.assertIsNotNone(_bmod._game_controller)
        stop_balatro()

    @patch("skills.balatro_skill.GameController", autospec=True)
    def test_stop_clears_controller(self, MockGC):
        """stop_balatro() clears _game_controller and _balatro_running."""
        MockGC.return_value.refresh_state.return_value = False
        iris = self._make_iris()
        start_balatro(iris, avatar=None)
        time.sleep(0.05)
        stop_balatro()
        time.sleep(0.1)
        with _bmod._balatro_lock:
            self.assertFalse(_bmod._balatro_running)
            self.assertIsNone(_bmod._game_controller)

    @patch("skills.balatro_skill.GameController", autospec=True)
    def test_double_start_is_idempotent(self, MockGC):
        """Calling start_balatro() twice must not spawn a second controller."""
        MockGC.return_value.refresh_state.return_value = False
        iris = self._make_iris()
        start_balatro(iris, avatar=None)
        first_controller = _bmod._game_controller
        start_balatro(iris, avatar=None)  # second call — should be ignored
        with _bmod._balatro_lock:
            self.assertIs(_bmod._game_controller, first_controller)
        stop_balatro()

    @patch("skills.balatro_skill.GameController", autospec=True)
    def test_restart_after_stop(self, MockGC):
        """After a clean stop, start_balatro() succeeds and creates a fresh controller."""
        MockGC.return_value.refresh_state.return_value = False
        iris = self._make_iris()
        start_balatro(iris, avatar=None)
        stop_balatro()
        time.sleep(0.1)
        MockGC.reset_mock()
        start_balatro(iris, avatar=None)
        time.sleep(0.05)
        with _bmod._balatro_lock:
            self.assertTrue(_bmod._balatro_running)
        stop_balatro()


# ---------------------------------------------------------------------------
# LLM-driven loop simulation
# ---------------------------------------------------------------------------


class TestBalatroLLMSimulation(unittest.TestCase):
    def setUp(self):
        stop_balatro()
        time.sleep(0.02)

    def _make_iris(self, responses):
        """Iris stub that returns scripted LLM outputs."""
        iris = MagicMock()
        iris.vm = DummyVM()

        def _chat_side_effect(*_, **__):
            if responses:
                result = responses.pop(0)
            else:
                result = "[continue]"
            return result

        iris.chat.side_effect = _chat_side_effect
        return iris

    @patch("skills.balatro_skill.time.sleep", autospec=True)
    @patch("skills.balatro_skill.BalatroAlgorithm.find_best_hand")
    @patch("skills.balatro_skill.GameController", autospec=True)
    def test_llm_drives_blind_shop_play(self, MockGC, mock_find_best, mock_sleep):
        """Simulate Iris running Balatro mode end-to-end without the full app."""
        mock_sleep.side_effect = lambda *_, **__: None
        controller = MockGC.return_value
        controller.client = MagicMock()
        controller.client.play_hand.return_value = (True, None)
        controller.client._call.return_value = {}
        controller.client.proceed_next.return_value = True

        mock_find_best.return_value = {"hand_name": "pair", "card_indices": [1, 2]}

        states = [
            {
                "state": "blind_select",
                "round": {"hands_left": 1, "discards_left": 1},
                "hand": {"count": 5, "cards": [{"rank": "A"}]},
                "jokers": [],
            },
            {
                "state": "shop",
                "round": {"hands_left": 1, "discards_left": 1},
                "hand": {"count": 5, "cards": [{"rank": "K"}]},
                "shop": {"count": 2, "cards": [{"id": 1, "label": "Joker"}]},
                "money": 10,
                "jokers": [],
            },
            {
                "state": "shop",  # simulate continued shop state after buying
                "round": {"hands_left": 1, "discards_left": 1},
                "hand": {"count": 5, "cards": [{"rank": "K"}]},
                "shop": {"count": 1, "cards": []},  # after buying
                "money": 5,  # spent money
                "jokers": [],
            },
            {
                "state": "playing",
                "round": {"hands_left": 1, "discards_left": 1},
                "hand": {
                    "count": 3,
                    "cards": [
                        {"rank": "2", "suit": "H"},
                        {"rank": "2", "suit": "D"},
                        {"rank": "K", "suit": "S"},
                    ],
                },
                "jokers": [],
            },
        ]

        def _refresh_state_side_effect():
            if not states:
                with _bmod._balatro_lock:
                    _bmod._balatro_running = False
                return False
            controller.raw_state = states.pop(0)
            return True

        controller.refresh_state.side_effect = _refresh_state_side_effect

        iris = self._make_iris(
            ["[continue]", "[buy_shop 1]", "[continue]", "[play_hand]"]
        )

        with _bmod._balatro_lock:
            _bmod._balatro_running = True
            _bmod._game_controller = controller

        _bmod.game_loop(iris, avatar=None)

        controller.client.proceed_next.assert_called_once()
        controller.client._call.assert_any_call("buy", {"index": 0})
        controller.client.play_hand.assert_called()


# ---------------------------------------------------------------------------
# Action tag parsing tests
# ---------------------------------------------------------------------------


class TestActionTagParsing(unittest.TestCase):
    _CASES = [
        # (response_string,          expected_tag,    expected_arg)
        ("[play_hand]", "play_hand", None),
        ("[Play Hand]", "play_hand", None),
        ("[PLAY_HAND]", "play_hand", None),
        ("[skip_blind]", "skip_blind", None),
        ("[SKIP]", "skip", None),
        ("[buy_shop 2]", "buy_shop", "2"),
        ("[Buy Shop 3]", "buy_shop", "3"),
        ("[Cash Out]", "cash_out", None),
        ("[CONTINUE]", "continue", None),
        ("No tag here at all.", "", None),
        ("", "", None),
    ]

    def test_all_cases(self):
        for response, expected_tag, expected_arg in self._CASES:
            with self.subTest(response=response):
                tag, arg = _parse_action(response)
                self.assertEqual(
                    tag, expected_tag, f"Tag mismatch for {response!r}: got {tag!r}"
                )
                self.assertEqual(
                    arg, expected_arg, f"Arg mismatch for {response!r}: got {arg!r}"
                )


# ---------------------------------------------------------------------------
# State hash tests
# ---------------------------------------------------------------------------


class TestStateHash(unittest.TestCase):
    def _make_state(self, hands_left=3, state="PLAYING"):
        return {
            "state": state,
            "round": {"hands_left": hands_left, "discards_left": 2},
            "hand": {"count": 5, "cards": [{"rank": "A", "suit": "S"}]},
            "jokers": [],
        }

    def test_identical_states_same_hash(self):
        s1 = self._make_state()
        s2 = self._make_state()
        self.assertEqual(_state_hash(s1), _state_hash(s2))

    def test_top_level_change_detected(self):
        s1 = self._make_state(state="PLAYING")
        s2 = self._make_state(state="SHOP")
        self.assertNotEqual(_state_hash(s1), _state_hash(s2))

    def test_nested_change_detected(self):
        """Shallow string concat would miss this; MD5 of full JSON catches it."""
        s1 = self._make_state(hands_left=3)
        s2 = self._make_state(hands_left=2)
        self.assertNotEqual(_state_hash(s1), _state_hash(s2))

    def test_deep_nested_joker_change_detected(self):
        s1 = self._make_state()
        s2 = self._make_state()
        s2["jokers"] = [{"name": "Joker", "sell_cost": 2}]
        self.assertNotEqual(_state_hash(s1), _state_hash(s2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
