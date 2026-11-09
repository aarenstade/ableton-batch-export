import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_catalog_workflow import make_set


class CliTests(unittest.TestCase):
    def test_first_run_preview_stop_and_finalize_without_live(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            config = base / "config.json"
            projects = base / "Projects"
            make_set(projects / "Example Project" / "Song.als")
            outputs = base / "Output"

            def command(*args, success=True):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "ableton_export.cli",
                        "--config",
                        str(config),
                        *args,
                    ],
                    cwd=base,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    result.returncode == 0, success, result.stdout + result.stderr
                )
                return result

            command("init", "--project-root", str(projects), "--output", str(outputs))
            command(
                "init",
                "--project-root",
                str(projects),
                "--output",
                str(outputs),
                success=False,
            )
            settings = json.loads(config.read_text())
            settings["minimum_free_bytes"] = (
                0  # Tiny synthetic fixtures need no 10-GiB reserve.
            )
            config.write_text(json.dumps(settings))
            command("index")
            command("audio-index")
            command("prepare")
            self.assertFalse((outputs / "queue.json").exists())
            command("prepare", "--write")
            command(
                "index", success=False
            )  # Do not silently replace the original hash baseline.
            command("stop")
            result = json.loads(command("finalize").stdout)
            self.assertEqual(result["completed_rows"], 0)
            self.assertEqual(result["unexported_rows"], 1)
            self.assertEqual(result["source_hashes_verified"], 1)
            self.assertIn(
                "Batch stopped", (outputs / "Unexported Projects.csv").read_text()
            )
            self.assertTrue((base / "run-state/STOP").is_file())


if __name__ == "__main__":
    unittest.main()
