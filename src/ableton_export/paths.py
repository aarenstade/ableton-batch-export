"""Stable source-relative output paths and a single catalog writer lock."""

import fcntl
import hashlib
from contextlib import contextmanager
from pathlib import Path


def source_relative(config, source):
    source = Path(source).resolve()
    roots = [Path(p) for p in config.project_roots]
    for root in roots:
        if source.is_relative_to(root):
            relative = source.relative_to(root)
            if len(roots) > 1:
                # Keep distinct roots separate even when their folder names match.
                label = (
                    root.name
                    + "__"
                    + hashlib.sha256(str(root).encode()).hexdigest()[:8]
                )
                relative = Path(label) / relative
            return relative
    raise ValueError(f"Source is outside the configured project roots: {source}")


@contextmanager
def batch_lock(config):
    state = Path(config.runtime_state_root)
    state.mkdir(parents=True, exist_ok=True)
    with (state / "batch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "Another batch operation is active; stop it and wait before editing the queue"
            ) from error
        yield
