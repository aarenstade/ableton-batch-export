"""Explicit CLI stages: index, prepare, export, stop, and finalize."""

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import Config
from .manifest import database, event, write_json
from .paths import batch_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="config.json", help="Paths resolve relative to this file"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create a local config without overwriting one")
    init.add_argument("--project-root", action="append", required=True)
    init.add_argument("--sample-root", action="append")
    init.add_argument("--output", required=True)
    init.add_argument("--app", default=Config.app_path)
    init.add_argument(
        "--unattended",
        action="store_true",
        help="Allow Live to come forward for UI actions",
    )
    doctor = sub.add_parser(
        "doctor", help="Check paths; --live also checks Accessibility and OSC"
    )
    doctor.add_argument("--live", action="store_true")
    for name in (
        "index",
        "audio-index",
        "status",
        "verify",
        "report",
        "finalize",
        "stop",
    ):
        sub.add_parser(name)
    prepare = sub.add_parser(
        "prepare",
        help="Preview sample resolution, then use --write to create working copies",
    )
    prepare.add_argument("--write", action="store_true")
    prepare.add_argument("--include-session", action="store_true")
    prepare.add_argument("--allow-missing-media", action="store_true")
    export = sub.add_parser(
        "export", help="Resume pending sets, then attempt previous failures once"
    )
    export.add_argument("--limit", type=int)
    export.add_argument("--id", action="append")
    export.add_argument("--detach", action="store_true")
    skip = sub.add_parser(
        "skip", help="Exclude a pending job by its queue ID; batch must be stopped"
    )
    skip.add_argument("id")
    skip.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        result = dispatch(args)
        if result is not None:
            print(json.dumps(result, indent=2, default=str))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        parser.exit(1, f"Error: {error}\n")


def dispatch(args):
    if args.command == "init":
        path = Path(args.config).expanduser().resolve()

        def absolute(p):
            return str(Path(p).expanduser().resolve())

        projects = [absolute(p) for p in args.project_root]
        config = Config(
            project_roots=projects,
            sample_roots=[absolute(p) for p in (args.sample_root or projects)],
            output_root=absolute(args.output),
            app_path=absolute(args.app),
            state_path=str(path.parent / "state.sqlite3"),
            runtime_state_root=str(path.parent / "run-state"),
            unattended_mode=args.unattended,
        )
        config.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            json.dump(dataclasses.asdict(config), stream, indent=2)
            stream.write("\n")
        return {"config": str(path)}
    config = Config.load(args.config)
    state = Path(config.runtime_state_root)
    if args.command == "stop":
        state.mkdir(parents=True, exist_ok=True)
        (state / "STOP").touch()
        write_json(
            state / "batch-stop.json",
            {"reason": "Operator requested stop", "stopped_at": time.time()},
        )
        return {
            "stop_requested": True,
            "note": "Wait for batch.json step=stopped and the worker to exit before finalizing",
        }
    if args.command == "status":
        result = {}
        for name, path in (
            ("batch", state / "batch.json"),
            ("catalog", Path(config.output_root) / "reports/status.json"),
        ):
            if path.exists():
                data = json.loads(path.read_text())
                result[name] = {
                    k: v for k, v in data.items() if k not in {"renders", "unexported"}
                }
        return result or {"state": "No batch reports yet"}
    if args.command == "export":
        if sys.platform != "darwin":
            raise RuntimeError("Native export requires macOS")
        config.require_sources()
        if args.limit is not None and args.limit <= 0:
            raise ValueError("--limit must be positive")
        if args.detach:
            state.mkdir(parents=True, exist_ok=True)
            command = [
                sys.executable,
                "-u",
                "-m",
                "ableton_export.cli",
                "--config",
                str(Path(args.config).resolve()),
                "export",
            ]
            if args.limit is not None:
                command.extend(["--limit", str(args.limit)])
            for job_id in args.id or []:
                command.extend(["--id", job_id])
            # The child acquires batch.lock. A second launch fails without taking over.
            with (state / "batch.log").open("ab") as log:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=dict(os.environ),
                )
            return {
                "pid": process.pid,
                "log": str(state / "batch.log"),
                "status": str(state / "batch.json"),
            }
        from .exporter import run

        with database(config.state_path) as db:
            return run(config, db, limit=args.limit, only_ids=args.id)
    with batch_lock(config):
        if args.command == "doctor":
            from .doctor import check

            result = check(config, live=args.live)
            if not result["ok"]:
                print(json.dumps(result, indent=2))
                raise RuntimeError("Preflight checks failed; see results above")
            return result
        with database(config.state_path) as db:
            if args.command in {"index", "audio-index", "prepare"}:
                config.require_sources()
            if args.command == "index":
                from .catalog import index_catalog

                if (Path(config.output_root) / "queue.json").exists():
                    raise RuntimeError(
                        "A queue already exists; preserve its original hash baseline and resume with export"
                    )
                return {"indexed": index_catalog(config, db)}
            if args.command == "audio-index":
                from .relink import index_audio

                return {"audio_files": index_audio(config, db)}
            if args.command == "prepare":
                from .preparation import prepare

                return prepare(
                    config,
                    db,
                    write=args.write,
                    include_session=args.include_session,
                    allow_missing_media=args.allow_missing_media,
                )
            if args.command == "skip":
                queue_path = Path(config.output_root) / "queue.json"
                queue = json.loads(queue_path.read_text())
                job = next((j for j in queue["jobs"] if j["id"] == args.id), None)
                if job is None:
                    raise ValueError("Unknown queue ID")
                receipt = state / "jobs" / (job["id"] + ".json")
                if (
                    receipt.exists()
                    and json.loads(receipt.read_text()).get("step") == "render_finished"
                ):
                    raise ValueError("Refusing to skip an already completed export")
                job["skip_reason"] = args.reason
                write_json(queue_path, queue)
                event(db, job["id"], "skipped_by_user", reason=args.reason)
            if args.command in {"verify", "finalize"}:
                from .verify import verify_all

                verified = verify_all(config, db)
                if args.command == "verify":
                    if verified.get("verification_failed"):
                        raise RuntimeError(
                            "WAV verification failed; see reports/verification.json"
                        )
                    return verified
            if args.command == "finalize":
                from .lookup import refresh

                return {"verification": verified, **refresh(config, db)}
            from .report import publish

            return publish(config, db)


if __name__ == "__main__":
    main()
