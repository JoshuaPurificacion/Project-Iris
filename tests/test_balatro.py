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
