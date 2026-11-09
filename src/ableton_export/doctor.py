"""Read-only preflight checks; never launch Live or change a document."""

import sys
from pathlib import Path


def check(config, *, live=False):
    checks = []

    def record(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    record("platform", sys.platform == "darwin", "Native rendering requires macOS")
    for root in config.project_roots + config.sample_roots:
        record("source_directory", Path(root).is_dir(), root)
    record("live_app", Path(config.app_path).is_dir(), config.app_path)
    if live and sys.platform == "darwin":
        from .macos import MacLive
        from .osc import query

        try:
            ui = MacLive(config)
            record(
                "accessibility",
                True,
                f"Bound Live process {ui.pid}; no UI action performed",
            )
        except RuntimeError as error:
            record("accessibility", False, str(error))
        try:
            record("osc", query(config, "/live/test") == ["ok"], "AbletonOSC responds")
            start = query(config, "/live/song/get/start_time")
            record(
                "session_start_time",
                bool(start),
                "Required start_time handler is available",
            )
        except (OSError, TimeoutError, ValueError) as error:
            record(
                "osc",
                False,
                f"Check AbletonOSC installation, start_time patch and ports: {error}",
            )
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
