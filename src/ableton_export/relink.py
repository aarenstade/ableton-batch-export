"""Conservative sample resolution and immutable-source working copies."""

import functools
import hashlib
import re
from pathlib import Path

from .catalog import walk_files

AUDIO_EXTENSIONS = {
    ".wav",
    ".aif",
    ".aiff",
    ".mp3",
    ".flac",
    ".ogg",
    ".m4a",
    ".mp4",
    ".rex",
    ".rx2",
    ".caf",
    ".sd2",
}


def index_audio(config, db):
    """Index names and byte sizes, reading no audio payloads."""
    count = 0
    db.execute(
        "CREATE TEMP TABLE audio_scan (path TEXT PRIMARY KEY, name TEXT, size INTEGER, mtime_ns INTEGER)"
    )
    for path in walk_files(config.sample_roots, Path(config.output_root).resolve()):
        if path.suffix.casefold() not in AUDIO_EXTENSIONS:
            continue
        stat = path.stat()
        db.execute(
            "INSERT OR REPLACE INTO audio_scan VALUES (?,?,?,?)",
            (str(path), path.name.casefold(), stat.st_size, stat.st_mtime_ns),
        )
        count += 1
        if count % 25000 == 0:
            print(f"Indexed {count} audio files", flush=True)
    db.execute("DELETE FROM audio")
    db.execute("INSERT INTO audio SELECT * FROM audio_scan")
    db.commit()
    return count


@functools.lru_cache(maxsize=4096)
def digest(path: str):
    """Compare ambiguous candidates by content rather than picking arbitrarily."""
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def suffix_score(left: str, right: str):
    """Count matching path components from filename toward the root."""
    score = 0
    for a, b in zip(
        reversed(left.replace("\\", "/").casefold().split("/")),
        reversed(right.replace("\\", "/").casefold().split("/")),
    ):
        if a != b:
            break
        score += 1
    return score


class Resolver:
    """Resolve only exact or unambiguous references; report uncertain matches."""

    def __init__(self, config, db):
        """Bind a completed audio index and the known source roots."""
        self.config = config
        self.db = db
        self.cache = {}

    def resolve(self, sample: dict, project_root: str):
        """Return path, evidence method, and unresolved candidate list."""
        key = (
            sample.get("absolute"),
            sample.get("relative"),
            sample.get("size"),
            project_root,
        )
        if key not in self.cache:
            self.cache[key] = self._resolve(sample, project_root)
        return self.cache[key]

    def _resolve(self, sample, project_root):
        if sample.get("error"):
            return {"status": "parse_error", "error": sample["error"]}
        size = sample["size"]
        absolute, relative = sample["absolute"], sample["relative"]
        direct = []
        if absolute:
            direct.append((Path(absolute), "original_absolute"))
        if relative:
            direct.append((Path(project_root) / relative, "project_relative"))
        for old in [absolute, relative]:
            normalized = old.replace("\\", "/")
            if "/Builtin/Samples/" in normalized:
                tail = normalized.split("/Builtin/Samples/", 1)[1]
                direct.append(
                    (
                        Path(self.config.app_path)
                        / "Contents/App-Resources/Builtin/Samples"
                        / tail,
                        "builtin_tree",
                    )
                )
        for path, method in direct:
            if path.is_file() and (size <= 0 or path.stat().st_size == size):
                return {
                    "status": "resolved",
                    "path": str(path.resolve()),
                    "method": method,
                }
        candidates = [
            dict(r)
            for r in self.db.execute(
                "SELECT path,size,mtime_ns FROM audio WHERE name=?",
                (sample["name"].casefold(),),
            )
        ]
        if size > 0:
            candidates = [r for r in candidates if r["size"] == size]
        if not candidates:
            return {"status": "missing", "candidates": []}
        for row in candidates:
            row["score"] = max(
                suffix_score(absolute, row["path"]), suffix_score(relative, row["path"])
            )
        best_score = max(r["score"] for r in candidates)
        best = [r for r in candidates if r["score"] == best_score]
        if size <= 0 and best_score < 2:
            if (
                len(best) == 1
                and sample.get("frames", 0) > 0
                and sample.get("sample_rate", 0) > 0
            ):
                candidate = best[0]
                if sample.get("mtime") == candidate["mtime_ns"] // 1_000_000_000:
                    import wave

                    try:
                        with wave.open(candidate["path"], "rb") as audio:
                            matches = (
                                audio.getnframes() == sample["frames"]
                                and audio.getframerate() == sample["sample_rate"]
                            )
                        if matches:
                            return {
                                "status": "resolved",
                                "path": candidate["path"],
                                "method": "name_mtime_frames",
                            }
                    except (wave.Error, OSError):
                        pass
            return {"status": "ambiguous", "candidates": [r["path"] for r in best]}
        if len(best) > 1:
            if (
                len({r["size"] for r in best}) > 1
                or len({digest(r["path"]) for r in best}) > 1
            ):
                return {"status": "ambiguous", "candidates": [r["path"] for r in best]}
        return {
            "status": "resolved",
            "path": sorted(r["path"] for r in best)[0],
            "method": "name_size" if size > 0 else "name_folder_trail",
            "equivalent_candidates": len(best),
        }


def safe_stem(name):
    """Create a recognizable portable output basename."""
    return re.sub(r"[^\w .()\-]+", "_", name).strip(". ")[:100] or "Untitled"
