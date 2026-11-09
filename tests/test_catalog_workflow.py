"""Offline integration tests using generated XML and PCM, never a user's catalog."""

import csv
import dataclasses
import gzip
import hashlib
import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from ableton_export.catalog import index_catalog, load_set
from ableton_export.config import Config
from ableton_export.lookup import refresh
from ableton_export.manifest import database, write_json
from ableton_export.paths import batch_lock, source_relative
from ableton_export.preparation import prepare
from ableton_export.relink import index_audio
from ableton_export.report import publish
from ableton_export.verify import inspect_wav, verify_all


def make_set(path, *, session=False, sample=None, armed=False):
    """Minimal XML for parser tests, not a claim of a natively playable Live set."""
    reference = ""
    if sample:
        reference = f'''<SampleRef><FileRef>
          <Path Value="/previous-machine/Samples/{sample.name}" />
          <RelativePath Value="Samples/{sample.name}" /><RelativePathType Value="1" />
          <OriginalFileSize Value="{sample.stat().st_size if sample.exists() else 100}" />
          <OriginalCrc Value="0" /><LivePackName Value="" /></FileRef>
          <DefaultDuration Value="0" /><DefaultSampleRate Value="44100" />
          </SampleRef>'''
    clip = f"""<MidiClip Time="0"><CurrentStart Value="0"/><CurrentEnd Value="4"/>
      <Name Value="Synthetic clip"/><Disabled Value="false"/>
      <Loop><LoopStart Value="0"/><LoopEnd Value="4"/></Loop>
      <FollowActionEnabled Value="true"/><FollowActionA Value="1"/>{reference}</MidiClip>"""
    content = (
        f"<ClipSlotList><ClipSlot>{clip}</ClipSlot></ClipSlotList>"
        if session
        else f"<ClipTimeable><ArrangerAutomation><Events>{clip}</Events></ArrangerAutomation></ClipTimeable>"
    )
    xml = f'''<Ableton Creator="Ableton Live 12.4.7" MajorVersion="5" MinorVersion="12.0_12049">
      <LiveSet><Tracks><MidiTrack><Name><EffectiveName Value="Synthetic"/></Name>
      <DeviceChain><IsArmed Value="{"true" if armed else "false"}"/><MainSequencer>{content}</MainSequencer></DeviceChain>
      </MidiTrack></Tracks><MainTrack><DeviceChain><Mixer><Tempo><Manual Value="120"/></Tempo></Mixer></DeviceChain></MainTrack>
      </LiveSet></Ableton>'''
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "Ableton Project Info").mkdir(exist_ok=True)
    path.write_bytes(gzip.compress(xml.encode(), mtime=0))


def make_wav(path, seconds=6):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(struct.pack("<hh", 1000, -1000) * int(44100 * seconds))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.projects = self.base / "Projects"
        self.samples = self.base / "Samples"
        self.projects.mkdir()
        self.samples.mkdir()
        self.config = Config(
            project_roots=[str(self.projects)],
            sample_roots=[str(self.samples), str(self.projects)],
            output_root=str(self.base / "Renders"),
            state_path=str(self.base / "state.sqlite3"),
            runtime_state_root=str(self.base / "runtime"),
            minimum_free_bytes=0,
        )
        self.root = Path(self.config.output_root)

    def test_index_relink_session_copies_and_resume_protection(self):
        media = self.samples / "kick.wav"
        make_wav(media, 1)
        arrangement = self.projects / "One Project" / "Sketch.als"
        session = self.projects / "Two Project" / "Session.als"
        backup = self.projects / "One Project" / "Backup" / "Sketch-old.als"
        make_set(arrangement, sample=media)
        make_set(session, session=True, armed=True)
        make_set(backup)
        originals = {p: p.read_bytes() for p in (arrangement, session, backup)}
        with database(self.config.state_path) as db:
            self.assertEqual(index_catalog(self.config, db), 3)
            index_audio(self.config, db)
            preview = prepare(self.config, db)
            self.assertEqual(preview["queued"], 1)
            self.assertFalse((self.root / "working").exists())
            result = prepare(self.config, db, write=True, include_session=True)
            self.assertEqual(result["queued"], 2)
            jobs = json.loads((self.root / "queue.json").read_text())["jobs"]
            session_job = next(j for j in jobs if j["mode"] == "session_performance")
            self.assertEqual(session_job["session_scenes"][0]["passes"], 3)
            document, _, _ = load_set(Path(session_job["working_path"]))
            self.assertTrue(
                all(e.get("Value") == "false" for e in document.root.iter("IsArmed"))
            )
            self.assertTrue(
                all(
                    e.get("Value") == "false"
                    for e in document.root.iter("FollowActionEnabled")
                )
            )
            first = next(j for j in jobs if j["mode"] == "arrangement")
            copy, _, _ = load_set(Path(first["working_path"]))
            self.assertEqual(
                copy.root.find(".//SampleRef/FileRef/Path").get("Value"), str(media)
            )
            self.assertEqual(
                Path(first["render_path"]).relative_to(self.root),
                Path("wav/One Project/Sketch.wav"),
            )
            with self.assertRaisesRegex(RuntimeError, "queue already exists"):
                prepare(self.config, db, write=True)
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)

    def test_partial_media_is_opt_in_and_flagged(self):
        source = self.projects / "One Project" / "Lost.als"
        make_set(source, sample=self.samples / "missing.wav")
        with database(self.config.state_path) as db:
            index_catalog(self.config, db)
            index_audio(self.config, db)
            self.assertEqual(prepare(self.config, db)["queued"], 0)
            self.assertEqual(
                prepare(self.config, db, write=True, allow_missing_media=True)[
                    "queued"
                ],
                1,
            )
            job = json.loads((self.root / "queue.json").read_text())["jobs"][0]
            self.assertTrue(job["allow_missing_media"])
            self.assertTrue(job["unresolved_samples"])
            self.assertIn("[missing media]", job["render_path"])

    def test_reports_cover_completed_excluded_skipped_failed_and_stopped(self):
        for name in ["Done", "Pending", "Skip", "Failed"]:
            make_set(self.projects / "One Project" / (name + ".als"))
        make_set(self.projects / "One Project" / "Session.als", session=True)
        with database(self.config.state_path) as db:
            index_catalog(self.config, db)
            prepare(self.config, db, write=True)
            queue_path = self.root / "queue.json"
            queue = json.loads(queue_path.read_text())
            for job in queue["jobs"]:
                name = Path(job["source"]).stem
                if name == "Done":
                    make_wav(Path(job["render_path"]))
                    write_json(
                        Path(self.config.runtime_state_root)
                        / "jobs"
                        / (job["id"] + ".json"),
                        {
                            "step": "render_finished",
                            "render_beats": 12,
                            "snapshot": {},
                            "status": "complete",
                            "audio": inspect_wav(Path(job["render_path"])),
                        },
                    )
                elif name == "Skip":
                    job["skip_reason"] = "Manual review"
                elif name == "Failed":
                    write_json(
                        Path(self.config.runtime_state_root)
                        / "jobs"
                        / (job["id"] + ".json"),
                        {"step": "failed", "error": "Synthetic timeout"},
                    )
            write_json(queue_path, queue)
            write_json(
                Path(self.config.runtime_state_root) / "batch-stop.json",
                {"reason": "Test stop"},
            )
            self.assertEqual(verify_all(self.config, db), {"complete": 1})
            summary = refresh(self.config, db)
            self.assertEqual(
                summary,
                {
                    "completed_rows": 1,
                    "unexported_rows": 4,
                    "source_hashes_verified": 5,
                },
            )
            for filename, expected in [
                ("Export to Source.csv", 1),
                ("Unexported Projects.csv", 4),
            ]:
                with (self.root / filename).open(
                    encoding="utf-8-sig", newline=""
                ) as stream:
                    self.assertEqual(len(list(csv.DictReader(stream))), expected)
            remaining = (self.root / "Unexported Projects.csv").read_text()
            for reason in (
                "Manual review",
                "Synthetic timeout",
                "Batch stopped",
                "session_not_enabled",
            ):
                self.assertIn(reason, remaining)
            playlist_paths = [
                p
                for p in (self.root / "Back Catalog.m3u8").read_text().splitlines()
                if not p.startswith("#")
            ]
            self.assertEqual(len(playlist_paths), 1)
            self.assertTrue(all((self.root / p).is_file() for p in playlist_paths))

    def test_missing_completed_wav_is_not_reported_as_success(self):
        make_set(self.projects / "Missing.als")
        with database(self.config.state_path) as db:
            index_catalog(self.config, db)
            prepare(self.config, db, write=True)
            job = json.loads((self.root / "queue.json").read_text())["jobs"][0]
            write_json(
                Path(self.config.runtime_state_root) / "jobs" / (job["id"] + ".json"),
                {"step": "render_finished", "render_beats": 12, "snapshot": {}},
            )
            self.assertEqual(verify_all(self.config, db), {"verification_failed": 1})
            status = publish(self.config, db)
            self.assertEqual(status["completed"], 0)
            self.assertEqual(status["failed"], 1)

    def test_existing_output_uses_collision_suffix_and_is_preserved(self):
        make_set(self.projects / "Song.als")
        existing = self.root / "wav/Song.wav"
        make_wav(existing)
        before = hashlib.sha256(existing.read_bytes()).hexdigest()
        with database(self.config.state_path) as db:
            index_catalog(self.config, db)
            prepare(self.config, db, write=True)
            job = json.loads((self.root / "queue.json").read_text())["jobs"][0]
            self.assertNotEqual(job["render_path"], str(existing))
            self.assertIn(job["id"], job["render_path"])
        self.assertEqual(hashlib.sha256(existing.read_bytes()).hexdigest(), before)

    def test_multiple_roots_and_lock(self):
        second = self.base / "Other" / "Projects"
        second.mkdir(parents=True)
        config = dataclasses.replace(
            self.config, project_roots=[str(self.projects), str(second)]
        )
        one = source_relative(config, self.projects / "Song.als")
        two = source_relative(config, second / "Song.als")
        self.assertNotEqual(one, two)
        with batch_lock(config):
            with self.assertRaisesRegex(RuntimeError, "Another batch operation"):
                with batch_lock(config):
                    pass


if __name__ == "__main__":
    unittest.main()
