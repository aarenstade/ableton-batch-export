"""Build relinked working copies and describe occupied Session scenes."""

import gzip
import hashlib
import math
import os
import shutil
from pathlib import Path

from .catalog import load_set, sample_refs, value
from .manifest import write_json
from .relink import safe_stem


def session_plan(document, tempo):
    scenes = {}
    tracks = [
        t for t in document.root.findall("./LiveSet/Tracks/*") if t.tag != "ReturnTrack"
    ]
    for track_index, track in enumerate(tracks):
        slots = track.findall("DeviceChain/MainSequencer/ClipSlotList/*")
        for scene_index, slot in enumerate(slots):
            for clip in slot.iter():
                if clip.tag not in {"AudioClip", "MidiClip"}:
                    continue
                start = float(value(clip, "Loop/LoopStart", "0"))
                end = float(value(clip, "Loop/LoopEnd", "4"))
                beats = max(0.25, end - start)
                if clip.tag == "AudioClip" and value(clip, "IsWarped") == "false":
                    beats *= tempo / 60
                scenes.setdefault(scene_index, []).append(
                    {
                        "track": track_index,
                        "scene": scene_index,
                        "name": value(clip, "Name"),
                        "estimated_beats": beats,
                    }
                )
    return [
        {
            "scene": s,
            "clips": clips,
            "passes": 3,
            "estimated_pass_beats": max(
                4, math.ceil(max(c["estimated_beats"] for c in clips) / 4) * 4
            ),
        }
        for s, clips in sorted(scenes.items())
    ]


def write_copy(record, report, document, destination):
    directory = destination.parent
    directory.mkdir(parents=True, exist_ok=True)
    info = directory / "Ableton Project Info"
    info.mkdir(exist_ok=True)
    for path in (Path(record["project_root"]) / "Ableton Project Info").glob("*.cfg"):
        if not (info / path.name).exists():
            shutil.copyfile(path, info / path.name)
    by_index = {s["index"]: s for s in report["samples"]}
    for index, ref, error in sample_refs(document):
        resolution = by_index.get(index, {})
        if error or ref is None or resolution.get("status") != "resolved":
            continue
        chosen = Path(resolution["path"])
        if document.version_tuple >= (11, 0, 0):
            ref.set_absolute(chosen)
            ref.set_relative_type(1)
        else:
            if "/" in ref.name or ":" in chosen.name:
                media = (
                    directory
                    / "Samples"
                    / "Imported"
                    / hashlib.sha256(str(chosen).encode()).hexdigest()[:12]
                )
                media.mkdir(parents=True, exist_ok=True)
                portable = media / (safe_stem(chosen.stem) + chosen.suffix)
                if not portable.exists():
                    shutil.copyfile(chosen, portable)
                if portable.stat().st_size != chosen.stat().st_size:
                    raise RuntimeError("Media copy differs")
                chosen = portable
                ref.sample_ref.find("FileRef/Name").set("Value", chosen.name)
            ref.set_relative_type(3)
            enabled = ref.sample_ref.find("FileRef/HasRelativePath")
            if enabled is not None:
                enabled.set("Value", "true")
        ref.set_relative(os.path.relpath(chosen, directory))
    payload = gzip.compress(document.generate_xml(), mtime=0)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise RuntimeError(f"Working copy already changed: {destination}")
    else:
        with destination.open("xb") as stream:
            stream.write(payload)
    validation, _, _ = load_set(destination)
    for index, ref, error in sample_refs(validation):
        if by_index.get(index, {}).get("status") == "resolved":
            if error or ref is None or not (ref.absolute_exists or ref.relative_exists):
                raise RuntimeError(f"Relinked reference failed validation: {index}")
    if (
        hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest()
        != record["source_hash"]
    ):
        raise RuntimeError("Original source changed")
    write_json(directory / "provenance.json", report)
