"""Portable configuration; relative paths are relative to the config file."""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    project_roots: list[str]
    output_root: str
    sample_roots: list[str] = field(default_factory=list)
    app_path: str = "/Applications/Ableton Live 12 Standard.app"
    state_path: str = "state.sqlite3"
    runtime_state_root: str = "run-state"
    osc_host: str = "127.0.0.1"
    osc_send_port: int = 11000
    osc_reply_port: int = 11001
    load_timeout_seconds: float = 120
    render_timeout_seconds: float = 600
    stable_seconds: float = 5
    poll_seconds: float = 1
    duration_tolerance_seconds: float = 2
    sample_rate: int = 44100
    bit_depth: int = 16
    tail_beats: float = 8
    minimum_free_bytes: int = 10 * 1024**3
    relaunch_every_n: int = 20
    working_revision: str = "v1"
    unattended_mode: bool = False
    session_stall_seconds: float = 20
    session_setup_timeout_seconds: float = 180
    render_stall_seconds: float = 90
    max_session_restarts: int = 1

    @classmethod
    def load(cls, path: str) -> "Config":
        config_file = Path(path).expanduser().resolve()
        data = json.loads(config_file.read_text())
        data.setdefault("state_path", "state.sqlite3")
        data.setdefault("runtime_state_root", "run-state")
        data.setdefault("app_path", cls.app_path)

        def absolute(value):
            p = Path(value).expanduser()
            return str((config_file.parent / p).resolve())

        for key in ("project_roots", "sample_roots"):
            values = data.get(key, data.get("project_roots", []))
            if not isinstance(values, list) or not all(
                isinstance(p, str) and p for p in values
            ):
                raise ValueError(f"{key} must be a list of nonempty paths")
            data[key] = list(dict.fromkeys(absolute(p) for p in values))
        for key in ("output_root", "state_path", "runtime_state_root", "app_path"):
            data[key] = absolute(data[key])
        config = cls(**data)
        config.validate()
        return config

    def validate(self):
        if not self.project_roots:
            raise ValueError("At least one project root is required")
        roots = [Path(p) for p in self.project_roots]
        for index, root in enumerate(roots):
            for other in roots[index + 1 :]:
                if root.is_relative_to(other) or other.is_relative_to(root):
                    raise ValueError("Project roots must not overlap")
            for output in (self.output_root, self.runtime_state_root, self.state_path):
                if Path(output).is_relative_to(root) or root.is_relative_to(
                    Path(output)
                ):
                    raise ValueError(
                        "Output, state and runtime paths must be outside project roots"
                    )
        if self.bit_depth != 16:
            raise ValueError("This verifier supports only 16-bit PCM WAV")
        if self.tail_beats < 0 or self.tail_beats % 4:
            raise ValueError("tail_beats must be a nonnegative multiple of four")
        if self.osc_host != "127.0.0.1":
            raise ValueError("OSC must use the local Live instance (127.0.0.1)")
        if not all(
            isinstance(p, int) and 1 <= p <= 65535
            for p in (self.osc_send_port, self.osc_reply_port)
        ):
            raise ValueError("OSC ports must be integers from 1 to 65535")
        if self.osc_send_port == self.osc_reply_port:
            raise ValueError("OSC send and reply ports must differ")
        positive = (
            self.load_timeout_seconds,
            self.render_timeout_seconds,
            self.stable_seconds,
            self.poll_seconds,
            self.duration_tolerance_seconds,
            self.session_stall_seconds,
            self.session_setup_timeout_seconds,
            self.render_stall_seconds,
            self.sample_rate,
            self.relaunch_every_n,
        )
        if not all(math.isfinite(v) and v > 0 for v in positive):
            raise ValueError(
                "Timeouts, polling, sample rate and relaunch interval must be positive"
            )
        if self.minimum_free_bytes < 0 or self.max_session_restarts < 0:
            raise ValueError("Free-space limit and restart count must be nonnegative")
        if (
            not self.working_revision
            or Path(self.working_revision).name != self.working_revision
            or self.working_revision in {".", ".."}
        ):
            raise ValueError("working_revision must be a single directory name")

    def require_sources(self):
        """Reject disconnected media roots instead of treating them as an empty catalog."""
        for root in self.project_roots + self.sample_roots:
            if not Path(root).is_dir():
                raise FileNotFoundError(
                    f"Source directory is not mounted or does not exist: {root}"
                )
