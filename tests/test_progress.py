import unittest

from ableton_export.progress import ProgressWatchdog


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.clock = lambda: self.now

    def test_stalled_transport_times_out(self):
        watcher = ProgressWatchdog(20, 600, self.clock)
        watcher.observe(16)
        self.now = 20
        with self.assertRaisesRegex(TimeoutError, "no progress"):
            watcher.observe(16)

    def test_continuous_long_performance_is_allowed(self):
        watcher = ProgressWatchdog(20, 1800, self.clock)
        for self.now in range(0, 1700, 10):
            watcher.observe(self.now * 2)

    def test_rewind_cannot_look_like_new_progress(self):
        watcher = ProgressWatchdog(20, 600, self.clock)
        watcher.observe(300)
        self.now = 1
        with self.assertRaisesRegex(TimeoutError, "rewound"):
            watcher.observe(0)

    def test_advancing_but_over_budget_times_out(self):
        watcher = ProgressWatchdog(20, 60, self.clock)
        for self.now in range(0, 60, 10):
            watcher.observe(self.now)
        self.now = 60
        with self.assertRaisesRegex(TimeoutError, "planned duration"):
            watcher.observe(60)


if __name__ == "__main__":
    unittest.main()
