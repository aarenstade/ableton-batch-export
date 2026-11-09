"""Bound a capture by actual progress and its planned performance duration."""

import time


class ProgressWatchdog:
    def __init__(self, stall_seconds, total_seconds, clock=time.monotonic):
        self.clock = clock
        self.started = self.changed_at = clock()
        self.stall_seconds = stall_seconds
        self.total_seconds = total_seconds
        self.previous = None

    def observe(self, position):
        now = self.clock()
        if self.previous is not None and position < self.previous - 0.05:
            raise TimeoutError("Session transport rewound; deferring this set")
        if self.previous is None or position > self.previous + 0.001:
            self.changed_at = now
            self.previous = position
        elif now - self.changed_at >= self.stall_seconds:
            raise TimeoutError(
                f"Session transport made no progress for {self.stall_seconds:g} seconds"
            )
        if now - self.started >= self.total_seconds:
            raise TimeoutError(
                "Session capture exceeded its planned duration plus recovery allowance"
            )
