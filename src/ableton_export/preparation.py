"""Prepare one immutable queue from the indexed main sets."""

import dataclasses
import json
import shutil
from collections import Counter
from pathlib import Path

from .catalog import load_set
from .manifest import event, write_json
from .paths import source_relative
from .relink import Resolver, safe_stem
from .working import session_plan, write_copy


def prepare(
    config, db, *, write=False, include_session=False, allow_missing_media=False
):
    config.require_sources()
    root = Path(config.output_root)
    if write and (root / "queue.json").exists():
        raise RuntimeError(
            "A queue already exists. Resume with export; use a new output directory to prepare another catalog"
        )
    root.mkdir(parents=True, exist_ok=True)
    resolver = Resolver(config, db)
    reports, jobs, occupied = [], [], set()
    for row in db.execute(
        "SELECT * FROM projects WHERE status != 'backup' ORDER BY path"
    ).fetchall():
        record = json.loads(row["record"])
        document, source_hash, _ = load_set(Path(record["path"]))
        if source_hash != record["source_hash"]:
            raise RuntimeError("Source changed since indexing: " + record["path"])
        samples = [
            {**s, **resolver.resolve(s, record["project_root"])}
            for s in record["samples"]
        ]
        missing = [s for s in samples if s["status"] != "resolved"]
        is_session = record["arrangement_end_beats"] <= 0
        scenes = session_plan(document, record["tempo"]) if is_session else []
        status = "ready"
        if is_session and not scenes:
            status = "no_playable_content"
        elif record["external_hardware"]:
            status = "external_hardware"
        elif is_session and not include_session:
            status = "session_not_enabled"
        elif missing and not allow_missing_media:
            status = "unresolved_samples"
        report = {
            "id": record["id"],
            "source": record["path"],
            "source_hash": source_hash,
            "plugins": record["plugins"],
            "samples": samples,
            "unresolved_count": len(missing),
            "status": status,
            "session_clip_count": record["session_clip_count"],
        }
        reports.append(report)
        if status != "ready":
            continue
        relative = source_relative(config, record["path"])
        suffix = " [missing media]" if missing else ""
        render = (
            root
            / "wav"
            / relative.parent
            / (safe_stem(relative.stem) + suffix + ".wav")
        )
        if str(render).casefold() in occupied or render.exists():
            render = render.with_name(render.stem + "__" + record["id"] + ".wav")
        if str(render).casefold() in occupied or render.exists():
            raise RuntimeError(f"Output collision; existing audio preserved: {render}")
        occupied.add(str(render).casefold())
        basename = safe_stem(relative.stem) + "__" + record["id"]
        destination = (
            root / "working" / config.working_revision / basename / (basename + ".als")
        )
        if is_session:
            for tag in ("FollowActionA", "FollowActionB", "IsArmed"):
                for element in document.root.iter(tag):
                    element.set("Value", "false" if tag == "IsArmed" else "0")
            for element in document.root.iter("FollowActionEnabled"):
                element.set("Value", "false")
        job = {
            "id": record["id"],
            "source": record["path"],
            "source_hash": source_hash,
            "working_path": str(destination),
            "render_path": str(render),
            "tempo": record["tempo"],
            "tempo_automated": record["tempo_automated"],
            "version": record["version"],
            "plugins": record["plugins"],
            "mode": "session_performance" if is_session else "arrangement",
            "allow_missing_media": bool(missing),
            "unresolved_samples": missing,
            "session_scenes": scenes,
            "end_beats": sum(s["estimated_pass_beats"] * s["passes"] for s in scenes)
            if is_session
            else record["arrangement_end_beats"],
        }
        if write:
            if shutil.disk_usage(root).free < config.minimum_free_bytes:
                raise RuntimeError("Insufficient free space")
            write_copy(record, report, document, destination)
            render.parent.mkdir(parents=True, exist_ok=True)
            event(db, record["id"], "prepared", working_path=str(destination))
        jobs.append(job)
    summary = dict(Counter(r["status"] for r in reports))
    write_json(
        root
        / "reports"
        / ("preparation.json" if write else "preparation-preview.json"),
        {"summary": summary, "sets": reports},
    )
    if write:
        jobs.sort(
            key=lambda j: (
                j["allow_missing_media"],
                j["mode"] == "session_performance",
                j["end_beats"],
            )
        )
        write_json(
            root / "queue.json", {"config": dataclasses.asdict(config), "jobs": jobs}
        )
    return {"queued": len(jobs), "written": write, "sets": summary}
