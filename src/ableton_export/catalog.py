"""Catalog arrangement content and samples without saving any source set."""

import dataclasses
import gzip
import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from abletoolz.live_set import AbletonSet
from abletoolz.live_set.sample_ref import SampleRef

from .manifest import event, put_project

logging.getLogger("abletoolz").setLevel(logging.CRITICAL)


def value(root, path, default=""):
    """Read a Live XML Value attribute without Element truthiness ambiguity."""
    element = root.find(path)
    return element.get("Value", default) if element is not None else default


def load_set(path: Path):
    """Return a read-only source document and its immutable source identity."""
    compressed = path.read_bytes()
    raw = (
        gzip.decompress(compressed)
        if compressed.startswith(b"\x1f\x8b")
        else compressed
    )
    document = AbletonSet(path)
    document._root = ET.fromstring(raw)
    document.project_root_folder = next(
        (p for p in path.parents if (p / "Ableton Project Info").is_dir()), path.parent
    )
    return (
        document,
        hashlib.sha256(compressed).hexdigest(),
        hashlib.sha256(raw).hexdigest(),
    )


def sample_refs(document):
    """Yield parsed sample references, explicitly marking unused placeholders."""
    for index, element in enumerate(document.root.iter("SampleRef")):
        file_ref = element.find("FileRef")
        if file_ref is None:
            continue
        if (
            file_ref.find("Path") is not None
            and not value(file_ref, "Path")
            and not value(file_ref, "RelativePath")
            and not value(file_ref, "LivePackName")
        ):
            yield index, None, None
            continue
        try:
            parsed = SampleRef.from_element(
                element, document.version_tuple, document.project_root_folder
            )
            yield index, parsed, None
        except Exception as exc:
            yield index, None, str(exc)


def describe(path: Path):
    """Extract only playable arrangement clips, excluding takes and grooves."""
    document, source_hash, xml_hash = load_set(path)
    root = document.root
    tracks = root.findall("./LiveSet/Tracks/*")
    arrangement = []
    session_count = 0
    for track in tracks:
        main = track.find("DeviceChain/MainSequencer")
        if main is None:
            continue
        for subtype in ["Sample", "ClipTimeable"]:
            for clip in main.findall(f"{subtype}/ArrangerAutomation/Events/*"):
                if clip.tag not in {"AudioClip", "MidiClip"}:
                    continue
                arrangement.append(
                    {
                        "track": value(track, "Name/EffectiveName"),
                        "type": clip.tag,
                        "start": float(
                            value(clip, "CurrentStart", clip.get("Time", "0"))
                        ),
                        "end": float(value(clip, "CurrentEnd", "0")),
                        "disabled": value(clip, "Disabled") == "true",
                    }
                )
        session_count += sum(
            1
            for e in main.findall(".//ClipSlotList/*")
            for c in e.iter()
            if c.tag in {"AudioClip", "MidiClip"}
        )
    master = root.find("./LiveSet/MainTrack")
    if master is None:
        master = root.find("./LiveSet/MasterTrack")
    tempo = (
        float(value(master, "DeviceChain/Mixer/Tempo/Manual", "120"))
        if master is not None
        else 120
    )
    end = max((c["end"] for c in arrangement if not c["disabled"]), default=0)
    tempo_automated = False
    if master is not None:
        target = master.find("DeviceChain/Mixer/Tempo/AutomationTarget")
        if target is not None:
            for envelope in master.findall(".//AutomationEnvelope"):
                if value(envelope, "EnvelopeTarget/PointeeId") == target.get("Id"):
                    tempo_automated = (
                        len(envelope.findall("Automation/Events/FloatEvent")) > 1
                    )
        tempo_automated |= (
            len(
                master.findall(
                    "DeviceChain/Mixer/Tempo/ArrangerAutomation/Events/FloatEvent"
                )
            )
            > 1
        )
    samples = []
    for index, ref, error in sample_refs(document):
        if error:
            samples.append({"index": index, "error": error, "status": "parse_error"})
        elif ref is not None:
            samples.append(
                {
                    "index": index,
                    "name": ref.name,
                    "absolute": str(ref.absolute or ""),
                    "relative": str(ref.relative or ""),
                    "size": ref.size,
                    "crc": ref.crc,
                    "mtime": ref.last_modified,
                    "frames": int(value(ref.sample_ref, "DefaultDuration", "0")),
                    "sample_rate": int(value(ref.sample_ref, "DefaultSampleRate", "0")),
                    "pack": ref.live_pack,
                    "pack_resolved": ref.pack_resolved,
                    "status": "online"
                    if ref.absolute_exists or ref.relative_exists
                    else "offline",
                }
            )
    try:
        plugins = [dataclasses.asdict(p) for p in document.plugins.scan([])]
        for plugin in plugins:
            for key in ["kind", "path", "alternative"]:
                plugin[key] = str(plugin[key]) if plugin[key] is not None else None
    except Exception as exc:
        plugins = [{"error": str(exc), "exists": None}]
    return {
        "id": hashlib.sha256(str(path).encode()).hexdigest()[:12],
        "path": str(path),
        "mtime_ns": path.stat().st_mtime_ns,
        "source_hash": source_hash,
        "xml_hash": xml_hash,
        "version": root.get("Creator"),
        "project_root": str(document.project_root_folder),
        "backup": any(
            p.lower() in {"backup", "backups", "abletoolz_backup"} for p in path.parts
        ),
        "tempo": tempo,
        "tempo_automated": tempo_automated,
        "track_count": len(tracks),
        "arrangement_end_beats": end,
        "estimated_seconds": end * 60 / tempo,
        "arrangement_clip_count": len(arrangement),
        "session_clip_count": session_count,
        "external_hardware": any(
            e.tag in {"ExternalInstrument", "ExternalAudioEffect"} for e in root.iter()
        ),
        "plugins": plugins,
        "samples": samples,
    }


def walk_files(roots, excluded):
    """Walk authorized roots, excluding hidden metadata, symlinks, and outputs."""
    for source in roots:
        source = Path(source)
        if not source.exists():
            continue

        def fail(error):
            raise error

        for directory, folders, names in os.walk(source, onerror=fail):
            folders[:] = [
                d
                for d in folders
                if not d.startswith(".")
                and not (Path(directory) / d).is_symlink()
                and not (Path(directory) / d).resolve().is_relative_to(excluded)
            ]
            for name in names:
                candidate = Path(directory) / name
                if not name.startswith(".") and not candidate.is_symlink():
                    yield candidate


def index_catalog(config, db):
    """Parse every original set and persist independent failures for triage."""
    paths = sorted(
        p
        for p in walk_files(config.project_roots, Path(config.output_root).resolve())
        if p.suffix.lower() == ".als"
    )
    for n, path in enumerate(paths):
        try:
            record = describe(path)
            status = (
                "backup"
                if record["backup"]
                else "empty"
                if record["arrangement_end_beats"] <= 0
                else "indexed"
            )
            put_project(db, record, status)
        except Exception as exc:
            event(db, str(path), "parse_failed", error=str(exc))
        if (n + 1) % 100 == 0:
            print(f"Indexed {n + 1}/{len(paths)}", flush=True)
    return len(paths)
