"""Validate finalized PCM WAVs and keep silence separate from export failures."""

import hashlib
import json
import math
import wave
from pathlib import Path

import numpy as np

from .manifest import event, write_json


def inspect_wav(path: Path):
    """Read all frames, detecting truncated files and measuring audio energy."""
    peak = 0.0
    sum_squares = 0.0
    samples = 0
    with wave.open(str(path), "rb") as wav:
        rate, channels, width = (
            wav.getframerate(),
            wav.getnchannels(),
            wav.getsampwidth(),
        )
        expected_frames = wav.getnframes()
        if width != 2:
            raise ValueError(f"Expected 16-bit PCM, found {width * 8} bits")
        read_frames = 0
        while block := wav.readframes(65536):
            data = np.frombuffer(block, dtype="<i2").astype(np.float64) / 32768
            peak = max(peak, float(np.max(np.abs(data))))
            sum_squares += float(np.dot(data, data))
            samples += len(data)
            read_frames += len(data) // channels
        if read_frames != expected_frames or read_frames <= 0:
            raise ValueError(
                f"Truncated or empty WAV: {read_frames}/{expected_frames} frames"
            )
    rms = math.sqrt(sum_squares / samples) if samples else 0.0
    return {
        "duration_seconds": read_frames / rate,
        "sample_rate": rate,
        "channels": channels,
        "bit_depth": width * 8,
        "frames": read_frames,
        "peak_dbfs": 20 * math.log10(max(peak, 1e-12)),
        "rms_dbfs": 20 * math.log10(max(rms, 1e-12)),
        "near_silent": peak < 0.0001,
    }


def verify_all(config, db):
    """Reconcile proven UI completion, complete WAV data, and source hashes."""
    root = Path(config.output_root)
    queue = json.loads((root / "queue.json").read_text())
    results = []
    for job in queue["jobs"]:
        output = Path(job["render_path"])
        completion = Path(config.runtime_state_root) / "jobs" / (job["id"] + ".json")
        if not completion.exists():
            continue
        outcome = json.loads(completion.read_text())
        if outcome.get("step") != "render_finished":
            continue
        result = {"id": job["id"], "source": job["source"], "render": str(output)}
        try:
            audio = inspect_wav(output)
            result.update(audio)
            if result["sample_rate"] != config.sample_rate or result["channels"] != 2:
                raise ValueError(
                    "WAV settings differ from configured stereo sample rate"
                )
            if job.get("mode") == "session_performance" or not job["tempo_automated"]:
                expected = outcome.get("snapshot", {}).get(
                    "session_expected_seconds",
                    outcome["render_beats"] * 60 / job["tempo"],
                )
                result["expected_seconds"] = expected
                if (
                    abs(result["duration_seconds"] - expected)
                    > config.duration_tolerance_seconds
                ):
                    raise ValueError(
                        f"Duration mismatch: {result['duration_seconds']} versus {expected}"
                    )
            if (
                hashlib.sha256(Path(job["source"]).read_bytes()).hexdigest()
                != job["source_hash"]
            ):
                raise ValueError("Source hash changed")
            result["status"] = "near_silent" if result["near_silent"] else "complete"
            if job.get("allow_missing_media"):
                result["status"] = "partial_missing_media" + (
                    "_near_silent" if result["near_silent"] else ""
                )
            outcome.update(
                audio=audio,
                status=result["status"],
                source=job["source"],
                plugins=job["plugins"],
            )
            write_json(completion, outcome)
            write_json(root / "reports" / "jobs" / (job["id"] + ".json"), outcome)
            db.execute(
                "UPDATE projects SET status=? WHERE id=?", (result["status"], job["id"])
            )
            event(db, job["id"], "verified", **result)
        except Exception as exc:
            result.update(status="verification_failed", error=str(exc))
            outcome.update(step="failed", status="verification_failed", error=str(exc))
            write_json(completion, outcome)
            write_json(root / "reports" / "jobs" / (job["id"] + ".json"), outcome)
            event(db, job["id"], "verification_failed", error=str(exc))
        results.append(result)
    write_json(root / "reports" / "verification.json", results)
    from collections import Counter

    return dict(Counter(r["status"] for r in results))
