# Configuration and operation

All commands accept `--config PATH` **before** the command:

```sh
ableton-export --config ./another-batch/config.json status
```

`init` writes a complete config and refuses to overwrite a file. Alternatively,
copy `config.example.json` to `config.json` and edit it. Relative paths resolve
against the config file's folder; `~` expands to your home directory.

| Option | Default / meaning |
|---|---|
| `project_roots` | Required; nonoverlapping source directories |
| `sample_roots` | Defaults to project roots; include relocated samples, packs and project audio |
| `output_root` | Required; outside source trees |
| `app_path` | `/Applications/Ableton Live 12 Standard.app` |
| `state_path` | `state.sqlite3`, relative to config |
| `runtime_state_root` | `run-state`, relative to config |
| `unattended_mode` | `false`; `init --unattended` sets true |
| `sample_rate` / `bit_depth` | `44100` / `16`; only 16-bit PCM is supported |
| `tail_beats` | `8`; must be a nonnegative multiple of four |
| `minimum_free_bytes` | 10 GiB; checked before copying/rendering |
| `working_revision` | `v1`; generated working-copy directory name |
| `load_timeout_seconds` | `120` |
| `session_setup_timeout_seconds` | `180` |
| `session_stall_seconds` | `20` without advancing transport |
| `render_stall_seconds` | `90` without file-size or displayed-progress change |
| `render_timeout_seconds` | `600` absolute export duration |
| `stable_seconds` / `poll_seconds` | `5` / `1` |
| `duration_tolerance_seconds` | `2` |
| `relaunch_every_n` | `20` successful renders |
| `max_session_restarts` | `1`, only for focus-sensitive mode |
| `osc_host` | `127.0.0.1`; local Live only |
| `osc_send_port` / `osc_reply_port` | `11000` / `11001` |

Session capture also has a total budget of planned playback time plus the greater
of 60 seconds or 20%. A long scene is allowed while transport advances within that
budget. If Live loses focus in focus-sensitive mode, the unsaved working-copy take
is replayed from the beginning once. In-place recording resume is deliberately
avoided because it can rewind or overwrite recorded clips.

Keep the same source paths, output root, state database and runtime directory when
resuming. IDs derive from absolute source paths. Moving a volume or regenerating a
queue is not a supported resume operation. Keep `queue.json`, working copies and
receipts together. Do not point two independent queues at the same Live instance
or OSC port even if they have different runtime folders.

`prepare` defaults to a preview that writes only a planning report. `--write`
creates copies and a queue; an existing queue is protected. It skips hardware
devices and truly empty sets, but does not deduplicate separate main set files.
`--include-session` and `--allow-missing-media` are preparation policies, not export
flags. The preview lists their effects before writing copies.

`export --id ID` selects a specific queued source. `--limit N` limits attempted
jobs, including failures; proven completed exports do not count against the limit.
Failures are retried only on a later invocation and after pending work. A hung set
may leave a partial WAV: move it to a separate recovery folder after the worker
exits before retrying that ID. Never delete a valid completed WAV to force a retry.

`status` reads existing snapshots without controlling Live. `report` refreshes
status and playlist from receipts; `verify` fully reads completed PCM files and
checks source hashes. `finalize` runs verification and generates both lookup CSVs,
including header-only CSVs when there are no rows. It does not start or resume a
worker, and it does not declare unfinished sets complete.

Preparation, verification, report updates and finalization take an exclusive batch
lock. Stop the worker and wait for exit before those operations. The `stop` command
only writes a marker; it is not a guarantee that a stalled native call has already
returned. Check the PID and `batch.json`; cleanup failures are logged.

Plugin references are a filesystem/metadata diagnostic. Live's runtime warnings
are also retained. Neither proves that every plugin or preset was restored. Tempo
automation prevents a simple constant-BPM duration comparison for Arrangement
sets; WAV format and source-integrity checks still apply.
