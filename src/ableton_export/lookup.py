"""Portable CSV lookup generation with no private services or Node dependencies."""

import csv
import hashlib
import json
import time
from pathlib import Path

from .manifest import write_json
from .paths import source_relative
from .report import collect, publish

EXPORT_HEADERS = [
    "Export file",
    "Project folder",
    "Export path",
    "Source project file",
    "Duration (seconds)",
    "Render status",
    "Missing plugin references",
    "Runtime warnings",
]
UNEXPORTED_HEADERS = [
    "Project folder",
    "Source set",
    "Source project file",
    "Not exported because",
    "Session clips",
    "Unresolved media references",
    "Missing sample names",
    "Missing sample paths",
]


def csv_value(value):
    # Spreadsheet applications may interpret arbitrary clip names as formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, headers, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows({k: csv_value(v) for k, v in row.items()} for row in rows)
    with temporary.open(encoding="utf-8-sig", newline="") as stream:
        parsed = list(csv.DictReader(stream))
    expected = [{k: str(csv_value(row.get(k, ""))) for k in headers} for row in rows]
    if parsed != expected:
        raise RuntimeError("CSV round-trip validation failed")
    temporary.replace(path)


def refresh(config, db):
    root, state = Path(config.output_root), Path(config.runtime_state_root)
    completed, remaining = collect(config, db)
    rows, unexported = [], []
    for job in sorted(completed, key=lambda j: j["source"].casefold()):
        source, output = Path(job["source"]), Path(job["render_path"])
        if not source.is_file() or not output.is_file():
            raise RuntimeError("Missing mapped source or export: " + job["source"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != job["source_hash"]:
            raise RuntimeError("Original source changed: " + str(source))
        warnings = list(job.get("live_warnings", []))
        if job.get("mode") == "session_performance":
            warnings.insert(
                0,
                f"Session performance: three passes of every occupied scene, top to bottom ({len(job['session_scenes'])} scenes).",
            )
        if job.get("unresolved_samples"):
            warnings.insert(
                0,
                "PARTIAL AUDIO: unavailable media: "
                + "; ".join(
                    sorted(
                        {
                            s.get("absolute") or s.get("name", "Unknown")
                            for s in job["unresolved_samples"]
                        }
                    )
                ),
            )
        rows.append(
            dict(
                zip(
                    EXPORT_HEADERS,
                    [
                        output.name,
                        str(source_relative(config, source).parent),
                        str(output),
                        str(source),
                        round(job["audio"]["duration_seconds"], 3),
                        job["status"],
                        "; ".join(
                            sorted(
                                {
                                    p.get("name", p.get("error", "Unknown"))
                                    for p in job["plugins"]
                                    if not p.get("exists")
                                }
                            )
                        ),
                        "\n".join(warnings),
                    ],
                )
            )
        )
    for job in sorted(remaining, key=lambda j: j["source"].casefold()):
        source = Path(job["source"])
        record = db.execute(
            "SELECT record FROM projects WHERE path=?", (str(source),)
        ).fetchone()
        count = json.loads(record[0])["session_clip_count"] if record else ""
        samples = job.get("unresolved_samples", [])
        unexported.append(
            dict(
                zip(
                    UNEXPORTED_HEADERS,
                    [
                        str(source_relative(config, source).parent),
                        source.stem,
                        str(source),
                        job["reason"],
                        count,
                        len(samples),
                        "; ".join(sorted({s.get("name", "Unknown") for s in samples})),
                        "\n".join(
                            sorted(
                                {
                                    s.get("absolute") or s.get("relative", "")
                                    for s in samples
                                }
                            )
                        ),
                    ],
                )
            )
        )
    checked, changed, unavailable = 0, [], []
    for row in db.execute("SELECT path, source_hash FROM projects"):
        source = Path(row["path"])
        if not source.is_file():
            unavailable.append(str(source))
        elif hashlib.sha256(source.read_bytes()).hexdigest() != row["source_hash"]:
            changed.append(str(source))
        else:
            checked += 1
    write_json(
        root / "reports/source-integrity.json",
        {
            "checked": checked,
            "changed": changed,
            "unavailable": unavailable,
            "verified_at": time.time(),
        },
    )
    if changed or unavailable:
        raise RuntimeError(
            "Source integrity check failed; see reports/source-integrity.json"
        )
    write_csv(root / "Export to Source.csv", EXPORT_HEADERS, rows)
    write_csv(root / "Unexported Projects.csv", UNEXPORTED_HEADERS, unexported)
    write_json(
        state / "export-lookup-data.json", {"headers": EXPORT_HEADERS, "rows": rows}
    )
    write_json(
        state / "unexported-lookup-data.json",
        {"headers": UNEXPORTED_HEADERS, "rows": unexported},
    )
    summary = publish(config, db)
    if len(rows) != summary["completed"]:
        raise RuntimeError("Lookup count differs from receipts")
    result = {
        "completed_rows": len(rows),
        "unexported_rows": len(unexported),
        "source_hashes_verified": checked,
    }
    write_json(
        root / "reports/lookup-refresh.json", {**result, "updated_at": time.time()}
    )
    return result
