"""Record a deterministic three-pass audition of each occupied Session scene."""

import math
import time

from .osc import query, send
from .progress import ProgressWatchdog


class SessionInterrupted(RuntimeError):
    """A stopped Session take must be replayed from a clean working copy."""


def record_session(exporter):
    from AppKit import NSWorkspace

    e = exporter
    config = e.config
    setup_deadline = float("inf")
    preparing = True

    def get(address, *args):
        e.ui.check_stop()
        if preparing and time.monotonic() > setup_deadline:
            raise TimeoutError("Session preparation exceeded its time limit")
        return query(config, address, args)[-1]

    def command(address, *args):
        send(config, address, args)

    def setting(name, value):
        command("/live/song/set/" + name, value)
        deadline = time.monotonic() + 3
        while get("/live/song/get/" + name) != value:
            if time.monotonic() > deadline:
                raise RuntimeError("Session setting did not stick: " + name)
            time.sleep(0.1)

    e.ui.guard()
    e.assert_loaded()
    setup_deadline = time.monotonic() + config.session_setup_timeout_seconds
    command("/live/song/stop_playing")
    command("/live/song/stop_all_clips", 0)
    for name, value in [
        ("record_mode", 0),
        ("session_record", 0),
        ("loop", 0),
        ("metronome", 0),
        ("punch_in", 0),
        ("punch_out", 0),
        ("arrangement_overdub", 0),
        ("clip_trigger_quantization", 4),
        ("start_time", 0.0),
        ("current_song_time", 0.0),
    ]:
        setting(name, value)
    num_tracks = int(get("/live/song/get/num_tracks"))
    clip_tracks = []
    for track in range(num_tracks):
        if get("/live/track/get/can_be_armed", track):
            command("/live/track/set/arm", track, 0)
            deadline = time.monotonic() + 3
            while get("/live/track/get/arm", track):
                if time.monotonic() > deadline:
                    raise RuntimeError("Could not disarm working-copy track")
                time.sleep(0.1)
        if get("/live/track/get/is_foldable", track):
            continue
        clip_tracks.append(track)
        if len(query(config, "/live/track/get/arrangement_clips/name", (track,))) > 1:
            raise RuntimeError("Session-only working copy already has an arrangement")
    # Enabling Arrangement Record starts transport; queue the first scene at bar 2.
    schedule = []
    position = 4
    seconds = 0
    tempo = e.snapshot["tempo"]
    for scene in e.job["session_scenes"]:
        index = scene["scene"]
        if get("/live/scene/get/time_signature_enabled", index):
            if (
                get("/live/scene/get/time_signature_numerator", index),
                get("/live/scene/get/time_signature_denominator", index),
            ) != (4, 4):
                raise RuntimeError("Session scene has a non-4/4 signature")
        if get("/live/scene/get/tempo_enabled", index):
            tempo = float(get("/live/scene/get/tempo", index))
        lengths = []
        for clip in scene["clips"]:
            track = clip["track"]
            length = float(get("/live/clip/get/length", track, index))
            if get("/live/clip/get/is_audio_clip", track, index) and not get(
                "/live/clip/get/warping", track, index
            ):
                length = float(get("/live/clip/get/end_marker", track, index)) - float(
                    get("/live/clip/get/start_marker", track, index)
                )
                length *= tempo / 60
            if not math.isfinite(length) or length <= 0:
                raise RuntimeError("Invalid Session clip length")
            lengths.append(length)
            for prop, value in [
                ("launch_quantization", 0),
                ("launch_mode", 0),
                ("legato", 0),
            ]:
                command("/live/clip/set/" + prop, track, index, value)
                time.sleep(0.1)
                if get("/live/clip/get/" + prop, track, index) != value:
                    raise RuntimeError("Clip launch setting did not stick")
        duration = max(4, math.ceil(max(lengths) / 4) * 4)
        for repeat in range(3):
            schedule.append(
                {
                    "scene": index,
                    "pass": repeat + 1,
                    "start_beats": position,
                    "duration_beats": duration,
                    "tempo": tempo,
                }
            )
            position += duration
            seconds += duration * 60 / tempo
    e.snapshot["session_schedule"] = schedule
    e.snapshot["session_expected_seconds"] = seconds + e.config.tail_beats * 60 / tempo
    e.snapshot["session_passes"] = 3
    e.snapshot["session_end_beats"] = position
    e.snapshot["render_start_beats"] = 4
    e.record(
        "recording_session",
        snapshot=e.snapshot,
        render_beats=position + e.config.tail_beats,
    )
    preparing = False
    watchdog = ProgressWatchdog(
        config.session_stall_seconds, seconds + max(60, seconds * 0.2)
    )
    last_progress = time.monotonic()

    def wait_until(target):
        nonlocal last_progress
        deadline = e.ui.clock() + max(180, (target + 16) * 60 / 20)
        while True:
            e.ui.check_stop()
            e.ui.refresh()
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if not config.unattended_mode and (
                not front or front.processIdentifier() != e.ui.pid
            ):
                command("/live/song/stop_playing")
                stopped_deadline = time.monotonic() + 3
                while get("/live/song/get/is_playing"):
                    if time.monotonic() > stopped_deadline:
                        raise RuntimeError("Could not pause Session playback")
                    time.sleep(0.1)
                e.ui.guard()
                # Live quantizes a stopped Arrangement Record restart. Replaying
                # the clean take avoids gaps, overwritten clips, and phase shifts.
                raise SessionInterrupted(
                    "Session recording paused after Live lost focus"
                )
            position_now = float(get("/live/song/get/current_song_time"))
            watchdog.observe(position_now)
            if position_now >= target:
                return position_now
            if e.ui.clock() > deadline:
                raise TimeoutError("Session playback stopped advancing")
            if time.monotonic() - last_progress > 20:
                e.status(
                    "recording_session",
                    beat=position_now,
                    total_beats=position,
                    scene_count=len(e.job["session_scenes"]),
                    elapsed_seconds=round(time.monotonic() - watchdog.started),
                    expected_capture_seconds=round(seconds),
                )
                last_progress = time.monotonic()
            time.sleep(0.08)

    try:
        setting("record_mode", 1)
        for index, entry in enumerate(schedule):
            if index:
                current = wait_until(entry["start_beats"] - 0.8)
                if current >= entry["start_beats"] - 0.05:
                    raise RuntimeError("Missed a quantized scene launch boundary")
            command("/live/scene/fire", entry["scene"], 0, 0)
            time.sleep(0.1)
        current = wait_until(position - 0.8)
        if current >= position - 0.05:
            raise RuntimeError("Missed the Session stop boundary")
        command("/live/song/stop_all_clips", 1)
        wait_until(position + 0.2)
    finally:
        command("/live/song/stop_playing")
        command("/live/song/set/record_mode", 0)
    setting("back_to_arranger", 0)
    arrangement = []
    for track in clip_tracks:
        names = query(config, "/live/track/get/arrangement_clips/name", (track,))[1:]
        starts = query(
            config, "/live/track/get/arrangement_clips/start_time", (track,)
        )[1:]
        lengths = query(config, "/live/track/get/arrangement_clips/length", (track,))[
            1:
        ]
        arrangement.extend(
            {"track": track, "name": name, "start": start, "length": length}
            for name, start, length in zip(names, starts, lengths)
        )
    e.snapshot["captured_arrangement"] = arrangement
    # Check that every intended source clip was actually captured at each launch.
    for entry in schedule:
        scene = next(s for s in e.job["session_scenes"] if s["scene"] == entry["scene"])
        for clip in scene["clips"]:
            matches = [
                c
                for c in arrangement
                if c["track"] == clip["track"]
                and abs(c["start"] - entry["start_beats"]) < 0.05
                and c["length"] > 0
            ]
            if not matches:
                raise RuntimeError(
                    f"Session clip not captured: scene {entry['scene']}, track {clip['track']}, pass {entry['pass']}"
                )
    e.render_bars = math.ceil((position - 4 + e.config.tail_beats) / 4)
    e.render_beats = e.render_bars * 4
    e.record("session_recorded", snapshot=e.snapshot, render_beats=e.render_beats)
