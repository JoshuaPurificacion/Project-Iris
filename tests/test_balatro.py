"""
test_balatro.py

Tests public compatibility shim (balatro_skill.py) and ensures safe import sequencing.
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

class TestBalatroShim(unittest.TestCase):
    @patch('skills.balatro_skill.start_session')
    def test_start_balatro_shim(self, mock_start_session):
        from skills.balatro_skill import start_balatro
        iris = MagicMock()
        avatar = MagicMock()
        start_balatro(iris, avatar)
        mock_start_session.assert_called_once_with(iris, avatar)

    @patch('skills.balatro_skill.stop_session')
    def test_stop_balatro_shim(self, mock_stop_session):
        from skills.balatro_skill import stop_balatro
        stop_balatro()
        mock_stop_session.assert_called_once()


class TestBalatroImportSafety(unittest.TestCase):
    def test_import_sequence_safety(self):
        """
        Ensures the shim, session, and (future) action registry can be imported
        sequentially without causing circular dependency lockups.
        """
        # Unload if loaded to simulate fresh boot
        for mod in list(sys.modules.keys()):
            if mod.startswith("skills.balatro"):
                del sys.modules[mod]

        try:
            import skills.balatro_skill
            import skills.balatro.session
            # Future: import skills.balatro.actions
        except ImportError as e:
            self.fail(f"Circular or missing import detected: {e}")


class TestBalatroClientRearrange(unittest.TestCase):
    def test_rearrange_uses_modern_zone_payload(self):
        from skills.balatro_client import BalatroClient

        client = BalatroClient()
        with patch.object(client, "_call", return_value={"ok": True}) as mock_call:
            ok = client.rearrange(1, 2, "consumeables")

        self.assertTrue(ok)
        mock_call.assert_called_once_with(
            "rearrange",
            {"card": 1, "to": 2, "consumables": True},
        )

    def test_rearrange_falls_back_to_legacy_location_payload(self):
        from skills.balatro_client import BalatroClient

        client = BalatroClient()
        with patch.object(client, "_call", side_effect=[None, {"ok": True}]) as mock_call:
            ok = client.rearrange(0, 1, "jokers")

        self.assertTrue(ok)
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(
            mock_call.call_args_list[0].args,
            ("rearrange", {"card": 0, "to": 1, "jokers": True}),
        )
        self.assertEqual(
            mock_call.call_args_list[1].args,
            ("rearrange", {"card": 0, "to": 1, "location": "jokers"}),
        )

    def test_rearrange_rejects_invalid_location(self):
        from skills.balatro_client import BalatroClient

        client = BalatroClient()
        with patch.object(client, "_call") as mock_call:
            ok = client.rearrange(0, 1, "invalid")

        self.assertFalse(ok)
        mock_call.assert_not_called()
        self.assertIn("Invalid rearrange location", client.last_error)
