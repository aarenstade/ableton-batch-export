"""Resumable unattended arrangement export with native macOS UI validation."""

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from .macos import MacLive, Stopped
from .manifest import event, write_json
from .osc import query, send
from .report import publish
from .verify import inspect_wav


class NeedsAttention(RuntimeError):
    """A UI state cannot safely be handled automatically."""


class Exporter:
    """Persist every transition and validate every resulting stereo WAV."""

    def __init__(self, config, db):
        self.config, self.db = config, db
        self.root = Path(config.output_root)
        self.state = Path(config.runtime_state_root)
        queue = json.loads((self.root / "queue.json").read_text())
        self.jobs = queue["jobs"]
        for field in (
            "project_roots",
            "output_root",
            "state_path",
            "runtime_state_root",
        ):
            if queue.get("config", {}).get(field) != getattr(config, field):
                raise ValueError("Queue configuration changed: " + field)
        for job in self.jobs:
            if (
                not Path(job["working_path"])
                .resolve()
                .is_relative_to((self.root / "working").resolve())
            ):
                raise ValueError("Queue working copy is outside this output directory")
            if (
                not Path(job["render_path"])
                .resolve()
                .is_relative_to((self.root / "wav").resolve())
            ):
                raise ValueError("Queue WAV is outside this output directory")
        self.job = None
        self.completed = 0
        self.failed = 0
        self.ui = None
        self.snapshot = None
        self.render_beats = None
        self.warnings = []
        self.last_owned_document = None
        self.log_path = max(
            (Path.home() / "Library/Preferences/Ableton").glob("Live */Log.txt"),
            key=lambda p: p.stat().st_mtime,
            default=None,
        )
        if self.log_path is None:
            raise RuntimeError(
                "Open Live once to initialize its preferences and log before exporting"
            )

    def status(self, step, **details):
        """Publish a small live heartbeat independently of per-job receipts."""
        record = {
            "pid": os.getpid(),
            "step": step,
            "updated_at": time.time(),
            "completed_this_run": self.completed,
            "failed_this_run": self.failed,
            "active": self.job["source"] if self.job else None,
            **details,
        }
        write_json(self.state / "batch.json", record)
        write_json(self.root / "reports" / "batch.json", record)
        print(json.dumps(record), flush=True)

    def record(self, step, **details):
        """Commit one named job transition to SQLite and mirrored JSON logs."""
        record = {
            "id": self.job["id"],
            "source": self.job["source"],
            "step": step,
            "recorded_at": time.time(),
            **details,
        }
        event(
            self.db,
            self.job["id"],
            step,
            **{k: v for k, v in record.items() if k != "step"},
        )
        write_json(self.state / "jobs" / (self.job["id"] + ".json"), record)
        write_json(self.root / "reports" / "jobs" / (self.job["id"] + ".json"), record)
        with (self.state / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        self.status(step)
        return record

    def owned(self):
        """Recognize only documents within this batch's generated working tree."""
        document = self.ui.document()
        if not document:
            return False
        path = Path(unquote(urlparse(document).path)).resolve()
        result = path.is_relative_to((self.root / "working").resolve())
        if result:
            self.last_owned_document = document
        return result

    def assert_loaded(self, nodes=None):
        if self.ui.document() != Path(self.job["working_path"]).as_uri():
            raise NeedsAttention("Loaded document is not the expected working copy")
        self.last_owned_document = self.ui.document()
        nodes = nodes if nodes is not None else self.ui.scan()
        text = " ".join(
            str(n.get(k, "")) for n in nodes for k in ("title", "description", "value")
        )
        if any(
            s in text.lower() for s in ("media files are missing", "sample offline")
        ):
            if not self.job.get("allow_missing_media"):
                raise RuntimeError(
                    "Live reports missing media despite filesystem verification"
                )
            warning = "Partial render: Live reports missing media; see unresolved_samples in the queue and receipt."
            if warning not in self.warnings:
                self.warnings.append(warning)

    def handle_dialog(self, nodes):
        """Dismiss only recognized informational or owned-document dialogs."""
        text = " ".join(
            str(n.get(k, "")) for n in nodes for k in ("title", "description", "value")
        )
        lower = text.lower()
        buttons = [n for n in nodes if n.get("role") == "AXButton"]

        def press_label(*labels):
            for label in labels:
                found = [
                    n
                    for n in buttons
                    if label in (n.get("title"), n.get("description"))
                ]
                if len(found) == 1:
                    self.ui.press(found[0])
                    return True
            return False

        if "some samples are not ready yet" in lower:
            if press_label("OK"):
                self.status("waiting_for_sample_decode")
                return True
        if ("plug-in" in lower or "plugin" in lower) and any(
            s in lower
            for s in (
                "could not be found",
                "could not be loaded",
                "not available",
                "missing",
                "disabled",
            )
        ):
            self.warnings.append(text)
            if press_label("OK"):
                return True
        if (
            self.job
            and self.job.get("allow_missing_media")
            and self.owned()
            and any(
                s in lower
                for s in (
                    "media files are missing",
                    "samples are missing",
                    "sample is missing",
                )
            )
        ):
            self.warnings.append("Partial render: " + text)
            if press_label("OK"):
                return True
        if (
            "save" in lower
            and ("changes" in lower or "before closing" in lower)
            and self.owned()
        ):
            if press_label("Don't Save", "Don’t Save"):
                return True
        if (
            self.last_owned_document
            and "recover" in lower
            and ("crash" in lower or "unexpectedly" in lower or "restore" in lower)
        ):
            self.warnings.append(text)
            if press_label("No", "Discard"):
                return True
        raise NeedsAttention("Unrecognized dialog: " + text[:2000])

    def close_export_dialogs(self):
        """Close abandoned export/save panels belonging to a working copy."""
        for _ in range(3):
            dialogs = self.ui.dialogs()
            if not dialogs or dialogs[0][0].get("title") not in (
                "Save",
                "Export Audio/Video",
            ):
                return
            if not self.owned():
                raise NeedsAttention("An unrelated document has an export dialog open")
            cancel = [
                n
                for n in dialogs[0]
                if n.get("role") == "AXButton"
                and "Cancel" in (n.get("title"), n.get("description"))
            ]
            if len(cancel) != 1:
                raise NeedsAttention("Cannot close the current export dialog")
            self.ui.press(cancel[0])

    def wait_loaded(self):
        deadline = self.ui.clock() + self.config.load_timeout_seconds
        while self.ui.clock() < deadline:
            self.ui.check_stop()
            dialogs = self.ui.dialogs()
            if dialogs:
                self.handle_dialog(dialogs[0])
            elif self.ui.document() == Path(self.job["working_path"]).as_uri():
                nodes = self.ui.scan()
                self.assert_loaded(nodes)
                try:
                    snapshot = {
                        "test": query(self.config, "/live/test"),
                        "log_offset": self.log_offset,
                    }
                    for name in (
                        "song_length",
                        "tempo",
                        "signature_numerator",
                        "signature_denominator",
                    ):
                        snapshot[name] = query(self.config, "/live/song/get/" + name)[0]
                except (TimeoutError, OSError):
                    time.sleep(self.config.poll_seconds)
                    continue
                if (
                    not self.job["tempo_automated"]
                    and abs(snapshot["tempo"] - self.job["tempo"]) > 0.05
                ):
                    raise RuntimeError("Loaded tempo differs from parsed source")
                if (
                    snapshot["signature_numerator"] != 4
                    or snapshot["signature_denominator"] != 4
                ):
                    raise RuntimeError(
                        "Non-4/4 export range requires separate validation"
                    )
                self.snapshot = snapshot
                self.render_bars = math.ceil(
                    (self.job["end_beats"] + self.config.tail_beats) / 4
                )
                self.render_beats = self.render_bars * 4
                back = [
                    n for n in nodes if n.get("description") == "Back to Arrangement"
                ]
                if back and int(back[0].get("value", 0)) == 1:
                    self.ui.press(back[0])
                return
            time.sleep(self.config.poll_seconds)
        raise TimeoutError("Project load timed out")

    def export_dialog(self):
        deadline = self.ui.clock() + self.config.load_timeout_seconds
        while self.ui.clock() < deadline:
            self.assert_loaded()
            self.ui.menu("Export Audio/Video...")
            time.sleep(0.3)
            dialogs = self.ui.dialogs()
            if dialogs and dialogs[0][0].get("title") == "Export Audio/Video":
                return
            if dialogs:
                self.handle_dialog(dialogs[0])
            time.sleep(self.config.poll_seconds)
        raise TimeoutError("Sample decoding/export dialog timed out")

    def configure(self):
        nodes = self.ui.scan()
        settings = {
            "Base.RenderedTrack": "Main",
            "Base.RenderAsLoopBox.RenderAsLoop": "Off",
            "Base.ConvertToMonoBox.ConvertToMono": "Off",
            "Base.NormalizeBox.Normalize": "Off",
            "Base.AnalysisFileBox.CreateAnalysisFile": "Off",
            "Base.SampleRateBox.SampleRate": str(self.config.sample_rate),
            "Base.EncodePcmBox.EncodePcm": "On",
            "Base.FileTypeBox.FileType": "WAV",
            "Base.FileTypeOptionsBox.FileTypeOptionsCardView.BitDepthBox.BitDepth": str(
                self.config.bit_depth
            ),
            "Base.EncodeMp3Box.EncodeMp3": "Off",
            "Base.VideoOptionsCardView.CreateVideoBox.CreateVideo": "Off",
        }
        for identifier, expected in settings.items():
            if str(self.ui.find(nodes, id=identifier).get("value")) != expected:
                raise NeedsAttention("Export setting changed: " + identifier)
        for identifier, value in (
            (
                "Base.RenderStartBox.RenderStart.Bars",
                1 + int(self.snapshot.get("render_start_beats", 0) / 4),
            ),
            ("Base.RenderStartBox.RenderStart.Beats", 1),
            ("Base.RenderStartBox.RenderStart.Subdivisions", 1),
            ("Base.RenderLengthBox.RenderLength.Bars", self.render_bars),
            ("Base.RenderLengthBox.RenderLength.Beats", 0),
            ("Base.RenderLengthBox.RenderLength.Subdivisions", 0),
        ):
            self.ui.number(identifier, value)
        self.record(
            "range_verified", render_beats=self.render_beats, snapshot=self.snapshot
        )
        self.ui.press(
            self.ui.find(self.ui.scan(), id="Base.FinalButtonsBox.ExportButton")
        )
        time.sleep(0.25)
        self.ui.find(self.ui.scan(), id="save-panel")

    def save(self):
        output = Path(self.job["render_path"])
        if output.exists():
            raise NeedsAttention("Output already exists; refusing replacement")
        self.ui.set_text(
            self.ui.find(self.ui.scan(), id="saveAsNameTextField"), output.name
        )
        self.ui.go_to(output.parent)
        nodes = self.ui.scan()
        if self.ui.find(nodes, id="saveAsNameTextField")["value"] != output.name:
            raise NeedsAttention("Save panel has a different filename")
        where = [
            n
            for n in nodes
            if n.get("role") == "AXPopUpButton" and n.get("value") == output.parent.name
        ]
        if len(where) != 1:
            raise NeedsAttention("Save panel has a different destination")
        self.record("rendering", render_beats=self.render_beats, snapshot=self.snapshot)
        self.ui.press(self.ui.find(nodes, role="AXButton", title="Save"))

    def wait_render(self):
        deadline = self.ui.clock() + self.config.render_timeout_seconds
        output = Path(self.job["render_path"])
        last_size, stable_since, heartbeat = 0, self.ui.clock(), 0
        last_progress = self.ui.clock()
        previous_progress = None
        while self.ui.clock() < deadline:
            self.ui.check_stop()
            size = output.stat().st_size if output.exists() else 0
            if size != last_size:
                last_size, stable_since = size, self.ui.clock()
                last_progress = self.ui.clock()
            dialogs = self.ui.dialogs()
            progress = tuple(
                (
                    str(n.get("id", "")),
                    str(n.get("value", "")),
                    str(n.get("title", "")),
                    str(n.get("description", "")),
                )
                for dialog in dialogs
                for n in dialog
                if n.get("role") == "AXProgressIndicator"
                or re.search(
                    r"\d+(?:\.\d+)?\s*%",
                    str(n.get("value", "")) + " " + str(n.get("title", "")),
                )
            )
            if progress != previous_progress:
                previous_progress = progress
                last_progress = self.ui.clock()
            if (
                size > 44
                and self.ui.clock() - stable_since >= self.config.stable_seconds
            ):
                if not dialogs:
                    self.assert_loaded()
                    return
            if self.ui.clock() - last_progress >= self.config.render_stall_seconds:
                raise TimeoutError(
                    f"Render made no file or percentage progress for {self.config.render_stall_seconds:g} seconds"
                )
            if time.monotonic() - heartbeat > 30:
                self.status("rendering", bytes_written=size)
                heartbeat = time.monotonic()
            time.sleep(self.config.poll_seconds)
        raise TimeoutError("Render timed out")

    def finish(self):
        audio = inspect_wav(Path(self.job["render_path"]))
        if audio["channels"] != 2 or audio["sample_rate"] != self.config.sample_rate:
            raise RuntimeError("Unexpected WAV format")
        expected = self.snapshot.get(
            "session_expected_seconds", self.render_beats * 60 / self.snapshot["tempo"]
        )
        if (
            self.job.get("mode") == "session_performance"
            or not self.job["tempo_automated"]
        ) and abs(
            audio["duration_seconds"] - expected
        ) > self.config.duration_tolerance_seconds:
            raise RuntimeError(
                f"Duration mismatch: {audio['duration_seconds']} versus {expected}"
            )
        if (
            hashlib.sha256(Path(self.job["source"]).read_bytes()).hexdigest()
            != self.job["source_hash"]
        ):
            raise NeedsAttention("Original source changed")
        with self.log_path.open("rb") as stream:
            stream.seek(
                min(self.snapshot.get("log_offset", 0), self.log_path.stat().st_size)
            )
            lines = stream.read().decode(errors="replace").splitlines()
        warnings = self.warnings + [
            line
            for line in lines
            if any(
                word in line.lower()
                for word in (
                    "missing",
                    "could not",
                    "failed",
                    "not found",
                    "not available",
                )
            )
        ]
        status = "near_silent" if audio["near_silent"] else "complete"
        if self.job.get("allow_missing_media"):
            status = "partial_missing_media" + (
                "_near_silent" if audio["near_silent"] else ""
            )
        self.db.execute(
            "UPDATE projects SET status=? WHERE id=?", (status, self.job["id"])
        )
        self.completed += 1
        self.record(
            "render_finished",
            status=status,
            audio=audio,
            snapshot=self.snapshot,
            render_beats=self.render_beats,
            live_warnings=warnings,
            plugins=self.job["plugins"],
            render_mode=self.job.get("mode", "arrangement"),
            unresolved_samples=self.job.get("unresolved_samples", []),
            duration_check="tempo_automation_present"
            if self.job["tempo_automated"]
            else "passed",
        )
        publish(self.config, self.db)

    def launch(self):
        subprocess.run(
            ["/usr/bin/open", "-a", self.config.app_path], check=True, timeout=10
        )
        deadline = time.monotonic() + self.config.load_timeout_seconds
        while time.monotonic() < deadline:
            try:
                self.ui = MacLive(self.config, self.status)
                self.ui.activate()
                for dialog in self.ui.dialogs():
                    self.handle_dialog(dialog)
                while time.monotonic() < deadline:
                    for dialog in self.ui.dialogs():
                        self.handle_dialog(dialog)
                    nodes = self.ui.scan()
                    if any(n.get("id") == "Transport.GlobalRecord" for n in nodes):
                        try:
                            if query(self.config, "/live/test") == ["ok"]:
                                return
                        except (TimeoutError, OSError):
                            pass
                    time.sleep(0.5)
                raise TimeoutError("Live startup did not reach a ready document")
            except RuntimeError as error:
                if isinstance(error, NeedsAttention):
                    raise
                time.sleep(1)
        raise TimeoutError("Live did not launch")

    def relaunch(self, force=False):
        """Restart only this batch's Live document; discard working-copy edits."""
        self.status("relaunching", force=force)
        if not self.ui.terminated():
            document = self.ui.document()
            if not self.owned() and document is not None:
                raise NeedsAttention("Refusing to quit an unrelated Live document")
            if force:
                if not self.last_owned_document:
                    raise NeedsAttention("No known batch document for timeout recovery")
                self.ui.app.forceTerminate()
            else:
                self.close_export_dialogs()
                for dialog in self.ui.dialogs():
                    if dialog[0].get("title") in ("Save", "Export Audio/Video"):
                        cancel = [
                            n
                            for n in dialog
                            if n.get("role") == "AXButton"
                            and "Cancel" in (n.get("title"), n.get("description"))
                        ]
                        if len(cancel) != 1:
                            raise NeedsAttention(
                                "Cannot close the current export dialog"
                            )
                        self.ui.press(cancel[0])
                    else:
                        self.handle_dialog(dialog)
                self.ui.menu("Quit Live")
                deadline = time.monotonic() + 15
                while not self.ui.terminated() and time.monotonic() < deadline:
                    for dialog in self.ui.dialogs():
                        self.handle_dialog(dialog)
                    time.sleep(0.3)
                if not self.ui.terminated():
                    self.ui.app.forceTerminate()
            deadline = time.monotonic() + 15
            while not self.ui.terminated() and time.monotonic() < deadline:
                time.sleep(0.2)
            if not self.ui.terminated():
                raise NeedsAttention("Live did not terminate")
        self.launch()

    def record_session_with_recovery(self):
        """Replay an interrupted Session take without publishing partial audio."""
        from .session import SessionInterrupted, record_session

        restarts = 0
        while True:
            self.snapshot["session_restarts_after_focus_pause"] = restarts
            try:
                record_session(self)
                return
            except SessionInterrupted:
                restarts += 1
                if restarts > self.config.max_session_restarts:
                    raise RuntimeError(
                        "Repeated focus interruptions; deferring this set after one restart"
                    )
                self.record("restarting_session_after_focus_pause", restarts=restarts)
                self.ui.guard()
                self.relaunch()
                self.ui.guard()
                subprocess.run(
                    [
                        "/usr/bin/open",
                        "-a",
                        self.config.app_path,
                        self.job["working_path"],
                    ],
                    check=True,
                    timeout=10,
                )
                self.wait_loaded()
                self.record(
                    "loaded", snapshot=self.snapshot, render_beats=self.render_beats
                )

    def stop_owned_playback(self):
        """Stop only a recognized working copy; do not save or discard other work."""
        if self.ui is None or self.ui.terminated() or not self.owned():
            return
        self.ui.stopping = True
        try:
            send(self.config, "/live/song/stop_playing")
            send(self.config, "/live/song/set/record_mode", (0,))
            # Stop must not wait indefinitely for focus in foreground-only mode.
            # OSC still stops playback; a remaining dialog can be closed manually.
            if self.config.unattended_mode:
                self.close_export_dialogs()
        finally:
            self.ui.stopping = False

    def run(self, limit=None, only_ids=None):
        """Resume a durable queue and keep running independently of the chat."""
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "batch.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (self.state / "STOP").unlink(missing_ok=True)
            (self.state / "batch-stop.json").unlink(missing_ok=True)
            write_json(
                self.state / "process.json",
                {"pid": os.getpid(), "started_at": time.time()},
            )
            awake = subprocess.Popen(
                ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]
            )
            try:
                try:
                    self.ui = MacLive(self.config, self.status)
                except RuntimeError:
                    self.launch()
                self.ui.guard()
                self.owned()

                def previously_failed(job):
                    receipt = self.state / "jobs" / (job["id"] + ".json")
                    return (
                        receipt.exists()
                        and json.loads(receipt.read_text()).get("step") == "failed"
                    )

                # Pending work gets a turn before another attempt at a prior failure.
                for job in sorted(self.jobs, key=previously_failed):
                    if job.get("skip_reason"):
                        continue
                    if only_ids and job["id"] not in only_ids:
                        continue
                    if limit is not None and self.completed + self.failed >= limit:
                        break
                    self.ui.check_stop()
                    receipt = self.state / "jobs" / (job["id"] + ".json")
                    prior = json.loads(receipt.read_text()) if receipt.exists() else {}
                    if (
                        prior.get("step") == "render_finished"
                        and Path(job["render_path"]).exists()
                    ):
                        continue
                    self.job, self.snapshot, self.render_beats, self.warnings = (
                        job,
                        None,
                        None,
                        [],
                    )
                    if Path(job["render_path"]).exists():
                        if prior.get("step") == "rendering" and prior.get("snapshot"):
                            self.snapshot, self.render_beats = (
                                prior["snapshot"],
                                prior["render_beats"],
                            )
                            try:
                                self.wait_render()
                                self.finish()
                                continue
                            except Exception as error:
                                self.record(
                                    "failed",
                                    error="Existing output preserved: " + str(error),
                                )
                        else:
                            self.record(
                                "failed",
                                error="Existing output has no completion receipt; preserved",
                            )
                        self.failed += 1
                        continue
                    if (
                        shutil.disk_usage(self.root).free
                        < self.config.minimum_free_bytes
                    ):
                        raise NeedsAttention("Insufficient free disk space")
                    if (
                        hashlib.sha256(Path(job["source"]).read_bytes()).hexdigest()
                        != job["source_hash"]
                    ):
                        raise NeedsAttention(
                            "Original source changed since catalog indexing"
                        )
                    try:
                        self.log_offset = self.log_path.stat().st_size
                        self.record("opening", plugins=job["plugins"])
                        self.close_export_dialogs()
                        for dialog in self.ui.dialogs():
                            self.handle_dialog(dialog)
                        self.ui.guard()
                        subprocess.run(
                            [
                                "/usr/bin/open",
                                "-a",
                                self.config.app_path,
                                job["working_path"],
                            ],
                            check=True,
                            timeout=10,
                        )
                        self.wait_loaded()
                        self.record(
                            "loaded",
                            snapshot=self.snapshot,
                            render_beats=self.render_beats,
                        )
                        if job.get("mode") == "session_performance":
                            self.record_session_with_recovery()
                        self.export_dialog()
                        self.configure()
                        self.save()
                        self.wait_render()
                        self.finish()
                    except Stopped:
                        raise
                    except Exception as error:
                        self.failed += 1
                        try:
                            accessibility = self.ui.diagnostic(self.ui.scan())
                        except Exception:
                            accessibility = []
                        self.record(
                            "failed",
                            error=str(error),
                            accessibility=accessibility,
                            snapshot=self.snapshot,
                            render_beats=self.render_beats,
                            plugins=job["plugins"],
                        )
                        publish(self.config, self.db)
                        if isinstance(error, NeedsAttention):
                            raise
                        self.relaunch(
                            force=isinstance(error, TimeoutError)
                            or self.ui.terminated()
                        )
                    if (
                        self.completed
                        and self.completed % self.config.relaunch_every_n == 0
                    ):
                        self.relaunch()
                self.job = None
                self.status("finished", summary=publish(self.config, self.db))
            except (Stopped, KeyboardInterrupt):
                (self.state / "STOP").touch()
                write_json(
                    self.state / "batch-stop.json",
                    {"reason": "Operator requested stop", "stopped_at": time.time()},
                )
                try:
                    self.stop_owned_playback()
                except Exception as error:
                    self.status("stop_cleanup_warning", error=str(error))
                self.status("stopped", summary=publish(self.config, self.db))
            except Exception as error:
                self.status(
                    "needs_attention",
                    error=str(error),
                    summary=publish(self.config, self.db),
                )
                raise
            finally:
                awake.terminate()


def run(config, db, limit=None, only_ids=None):
    """Start a single locked queue consumer."""
    Exporter(config, db).run(limit, only_ids)
