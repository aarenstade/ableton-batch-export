# Architecture

```text
original .als ── index + SHA-256 ── SQLite manifest
sample roots ── filename/size index ── conservative resolver
                         │
                         ▼
              relinked working copies + queue.json
                         │
            AbletonOSC + macOS Accessibility
                         │
                         ▼
         native Main WAV export + durable completion receipt
                         │
                         ▼
          PCM verification + source hash + CSVs + playlist
```

| Module | Responsibility |
|---|---|
| `config`, `paths`, `cli` | Portable paths, explicit commands, catalog lock |
| `catalog`, `manifest` | Read-only source inspection, SQLite state/events |
| `relink`, `working`, `preparation` | Sample matching, Session plans, verified copies |
| `osc` | Bounded localhost UDP requests to a separately installed AbletonOSC |
| `macos` | App/PID-bound Accessibility, Quartz and guarded System Events actions |
| `session`, `progress` | Three scene passes, capture checks and progress watchdog |
| `exporter` | Load/export state machine, recovery, receipts and stop handling |
| `verify`, `report`, `lookup` | PCM validation, honest status, paths and CSV output |

The driver validates the loaded document URL against the expected generated set.
It refuses to overwrite audio, and only quits/restarts a Live document recognized
as a working copy. Known plugin informational dialogs can be dismissed; unknown
dialogs stop for attention. After an owned process crashes, known recovery dialogs
can discard its disposable recovery state. The tool does not repair or replace
plugins, control external hardware, or save source sets.

The queue maps source identities to working copies and final output names.
`run-state/jobs/<id>.json` stores the latest transition; `events.jsonl` and SQLite
retain earlier events. Receipts are mirrored under output `reports/jobs/`.
A `render_finished` receipt plus the WAV is required for completion. A filename
alone is insufficient. Verification failures remove that completion claim.

XML manipulation uses the third-party `abletoolz` parser. Its `_root` attribute is
used to load decompressed XML without invoking a source save; this private API is
one reason the dependency is pinned. Sample relinking changes only a new generated
document. Session preparation also clears stored track-arm flags before Live loads
the copy, including cases where a disabled audio-input arm control cannot be
changed through OSC.

This repository consolidates the reusable standalone driver. Earlier chat-bound
adapters, catalog-specific migration scripts, emergency repair runners, private
spreadsheet libraries and one-project experiments are intentionally omitted. Their
reusable fixes are incorporated into the preparation and export paths.

The test suite never manipulates Live. A native smoke test still requires the
documented macOS setup and an operator-owned disposable set. When making UI changes,
record the Live version, one-set export result, format/duration checks and source
hash before claiming native compatibility.
