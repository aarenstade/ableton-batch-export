"""Report every main source and publish a playlist from completion receipts."""

import json
from collections import Counter
from pathlib import Path

from .manifest import write_json


def collect(config, db):
    root, state = Path(config.output_root), Path(config.runtime_state_root)
    queue = json.loads((root / "queue.json").read_text())["jobs"]
    plan = json.loads((root / "reports/preparation.json").read_text())
    completed, remaining = [], []
    stopped = (state / "batch-stop.json").exists()
    for job in queue:
        receipt = state / "jobs" / (job["id"] + ".json")
        outcome = json.loads(receipt.read_text()) if receipt.exists() else {}
        step = outcome.get("step")
        if (
            step == "render_finished"
            and Path(job["render_path"]).is_file()
            and not job.get("skip_reason")
        ):
            completed.append(
                {
                    **job,
                    "audio": outcome.get("audio", {}),
                    "status": outcome.get("status", "complete"),
                    "live_warnings": outcome.get("live_warnings", []),
                }
            )
            continue
        if job.get("skip_reason"):
            reason, category = (
                "Skipped by operator: " + job["skip_reason"],
                "skipped_by_user",
            )
        elif step == "render_finished":
            reason, category = "Completion receipt exists but WAV is missing", "failed"
        elif step == "failed":
            reason, category = (
                "Export failed: " + str(outcome.get("error", "Unknown error")),
                "failed",
            )
        else:
            category = "pending"
            reason = (
                "Batch stopped by operator before export"
                if stopped
                else "Queued: " + job.get("mode", "arrangement")
            )
        remaining.append({**job, "reason": reason, "category": category})
    for record in plan["sets"]:
        if record["status"] != "ready":
            remaining.append(
                {
                    **record,
                    "reason": record["status"],
                    "category": "excluded",
                    "unresolved_samples": [
                        s for s in record["samples"] if s["status"] != "resolved"
                    ],
                }
            )
    # Parsing errors have no usable project row; still expose them to the operator.
    known = {j["source"] for j in completed + remaining}
    for row in db.execute(
        "SELECT project_id, details FROM events WHERE step='parse_failed' ORDER BY sequence DESC"
    ):
        if row["project_id"] not in known:
            path = Path(row["project_id"])
            if any(
                p.casefold() in {"backup", "backups", "abletoolz_backup"}
                for p in path.parts
            ):
                continue
            remaining.append(
                {
                    "source": str(path),
                    "reason": "Parse failed: " + json.loads(row["details"])["error"],
                    "category": "excluded",
                    "unresolved_samples": [],
                }
            )
            known.add(str(path))
    return completed, remaining


def publish(config, db):
    root, state = Path(config.output_root), Path(config.runtime_state_root)
    completed, remaining = collect(config, db)
    completed.sort(key=lambda j: j["source"].casefold())
    counts = Counter(j["category"] for j in remaining)
    lines = ["#EXTM3U"]
    for job in completed:
        seconds = int(job.get("audio", {}).get("duration_seconds", -1))
        title = Path(job["source"]).stem.replace("\n", " ").replace("\r", " ")
        lines.extend(
            [
                f"#EXTINF:{seconds},{title}",
                str(Path(job["render_path"]).relative_to(root)),
            ]
        )
    temporary = root / "Back Catalog.m3u8.tmp"
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(root / "Back Catalog.m3u8")
    status = {
        "total_main_sets": len(completed) + len(remaining),
        "completed": len(completed),
        "pending": counts["pending"],
        "failed": counts["failed"],
        "skipped_by_user": counts["skipped_by_user"],
        "excluded": counts["excluded"],
        "render_statuses": dict(Counter(j["status"] for j in completed)),
        "catalog_counts": dict(
            db.execute(
                "SELECT status, count(*) FROM projects GROUP BY status"
            ).fetchall()
        ),
        "renders": completed,
        "unexported": remaining,
    }
    if (state / "batch-stop.json").exists():
        status["batch_state"] = "stopped_by_user"
    write_json(root / "reports/status.json", status)
    return {k: v for k, v in status.items() if k not in {"renders", "unexported"}}
