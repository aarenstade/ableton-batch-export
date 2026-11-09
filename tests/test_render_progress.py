import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ableton_export.exporter import Exporter


class RenderProgressTests(unittest.TestCase):
    def make_exporter(self, size, dialogs):
        self.now = 0
        e = Exporter.__new__(Exporter)
        e.config = SimpleNamespace(
            render_timeout_seconds=600,
            render_stall_seconds=90,
            stable_seconds=5,
            poll_seconds=1,
        )
        e.job = {"render_path": "/unused/test.wav"}
        e.status = lambda *args, **kwargs: None
        e.assert_loaded = lambda: None
        e.ui = SimpleNamespace(
            clock=lambda: self.now,
            check_stop=lambda: None,
            dialogs=lambda: dialogs(self.now),
        )
        path = SimpleNamespace(
            exists=lambda: True, stat=lambda: SimpleNamespace(st_size=size(self.now))
        )
        return e, path

    def sleep(self, seconds):
        self.now += seconds

    def test_empty_frozen_export_moves_on_after_90_seconds(self):
        e, path = self.make_exporter(lambda t: 0, lambda t: [[{"role": "AXDialog"}]])
        with (
            patch("ableton_export.exporter.Path", return_value=path),
            patch("ableton_export.exporter.time.sleep", side_effect=self.sleep),
        ):
            with self.assertRaisesRegex(TimeoutError, "no file or percentage progress"):
                e.wait_render()
        self.assertEqual(self.now, 90)

    def test_growing_export_can_take_more_than_90_seconds(self):
        e, path = self.make_exporter(
            lambda t: 100 + min(int(t // 20), 6) * 100,
            lambda t: [[{"role": "AXDialog"}]] if t < 125 else [],
        )
        with (
            patch("ableton_export.exporter.Path", return_value=path),
            patch("ableton_export.exporter.time.sleep", side_effect=self.sleep),
        ):
            e.wait_render()
        self.assertEqual(self.now, 125)

    def test_live_percentage_progress_counts_before_file_is_written(self):
        e, path = self.make_exporter(
            lambda t: 0 if t < 120 else 1000,
            lambda t: (
                [[{"role": "AXProgressIndicator", "value": str(t)}]] if t < 125 else []
            ),
        )
        with (
            patch("ableton_export.exporter.Path", return_value=path),
            patch("ableton_export.exporter.time.sleep", side_effect=self.sleep),
        ):
            e.wait_render()
        self.assertEqual(self.now, 125)


if __name__ == "__main__":
    unittest.main()
