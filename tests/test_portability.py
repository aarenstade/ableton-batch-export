import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from ableton_export.config import Config
from ableton_export.lookup import write_csv


class PortabilityTests(unittest.TestCase):
    def test_config_paths_are_relative_to_config_not_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            path = base / "config.json"
            path.write_text(
                json.dumps({"project_roots": ["projects"], "output_root": "exports"})
            )
            config = Config.load(str(path))
            self.assertEqual(config.project_roots, [str(base / "projects")])
            self.assertEqual(config.sample_roots, config.project_roots)
            self.assertEqual(config.state_path, str(base / "state.sqlite3"))
            with self.assertRaisesRegex(FileNotFoundError, "not mounted"):
                config.require_sources()

    def test_sources_cannot_contain_outputs_and_unsupported_bit_depth_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for data, reason in [
                (
                    {"project_roots": ["projects"], "output_root": "projects/renders"},
                    "outside project roots",
                ),
                (
                    {
                        "project_roots": ["projects"],
                        "output_root": "renders",
                        "bit_depth": 24,
                    },
                    "16-bit",
                ),
                (
                    {
                        "project_roots": ["projects", "projects/child"],
                        "output_root": "renders",
                    },
                    "overlap",
                ),
            ]:
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, reason):
                    Config.load(str(path))

    def test_csv_preserves_unicode_quotes_newlines_and_empty_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lookup.csv"
            rows = [
                {"name": '=HYPERLINK("bad")', "note": 'Café, "quoted"\nsecond line'}
            ]
            write_csv(path, ["name", "note"], rows)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                result = list(csv.DictReader(stream))
            self.assertEqual(result[0]["name"], "'" + rows[0]["name"])
            self.assertEqual(result[0]["note"], rows[0]["note"])
            write_csv(path, ["name", "note"], [])
            with path.open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(list(csv.reader(stream)), [["name", "note"]])

    def test_osc_patch_is_idempotent_and_rejects_unknown_layout(self):
        script = Path(__file__).resolve().parents[1] / "scripts/patch_abletonosc.py"
        spec = importlib.util.spec_from_file_location("osc_patch", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = 'class Song:\n    def api(self):\n        properties_rw = [\n            "tempo",\n        ]\n'
        patched = module.patched_source(original)
        self.assertIn('"start_time"', patched)
        self.assertEqual(module.patched_source(patched), patched)
        with self.assertRaises(ValueError):
            module.patched_source("properties_rw = ()\n")


if __name__ == "__main__":
    unittest.main()
