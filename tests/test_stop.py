import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ableton_export.exporter import Exporter


class StopTests(unittest.TestCase):
    def test_foreground_only_stop_does_not_wait_for_focus(self):
        exporter = Exporter.__new__(Exporter)
        exporter.ui = SimpleNamespace(terminated=lambda: False)
        exporter.config = SimpleNamespace(unattended_mode=False)
        exporter.owned = lambda: True
        exporter.close_export_dialogs = Mock()
        with patch("ableton_export.exporter.send") as send:
            exporter.stop_owned_playback()
        self.assertEqual(send.call_count, 2)
        exporter.close_export_dialogs.assert_not_called()
        self.assertFalse(exporter.ui.stopping)

    def test_stop_does_not_control_an_unrelated_document(self):
        exporter = Exporter.__new__(Exporter)
        exporter.ui = SimpleNamespace(terminated=lambda: False)
        exporter.owned = lambda: False
        with patch("ableton_export.exporter.send") as send:
            exporter.stop_owned_playback()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
