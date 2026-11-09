# Troubleshooting

| Symptom | What to inspect |
|---|---|
| Source directory missing | Mount the drive and verify `project_roots` / `sample_roots`; indexing refuses disconnected roots |
| Accessibility unavailable | Grant permission to the actual launching terminal/application, then reopen it |
| OSC timeout | Select AbletonOSC in Live; check ports, one Live instance and the `start_time` patch |
| Address already in use | Another OSC client or worker owns reply port 11001; stop that client |
| Export setting changed | Set the exact preferences in `docs/setup.md`, cancel the dialog, then retry |
| Unrecognized dialog | Read `run-state/batch.json` and the job receipt; handle it manually before resuming |
| Working copy already changed | Preserve the existing copy and use a fresh batch output; do not rewrite it in place |
| Source hash changed | Investigate the original; do not accept a new baseline merely to hide the change |
| Existing output has no completion receipt | Preserve it in a recovery folder, inspect/listen, then retry the ID if needed |
| Render or Session timeout | Check Live's log, plugins, device load and the receipt; pending work should proceed first |
| Non-4/4 range | This release does not implement that meter; render manually |
| Quiet or incomplete result | Inspect missing-media/plugin notes; `near_silent` and partial statuses remain separate from success |

Missing media is never made complete by suppressing a dialog. Add all available
sample locations, rebuild in a fresh batch directory, and inspect resolution
evidence before exporting. If deliberately using `--allow-missing-media`, expect
missing instruments/clips or silence; keep the partial status with the audio.

While Session transport is advancing, the tool may spend minutes on a single set.
The log includes current beat, total beats and expected capture time. Background
playback is enabled only with `unattended_mode: true`; otherwise focus changes
cause a bounded clean replay of the take.

`finished` in the worker status means that invocation ended, not that every source
rendered. Check `completed`, `pending`, `failed`, `excluded` and `skipped_by_user` in
the summary. The unexported CSV accounts for main-set exclusions as well as queued
jobs left incomplete. Backup/history sets are cataloged for integrity checks but
are intentionally not listed as missing main exports.

Reports and logs contain local source paths and may include plugin names, clip
names and dialog text. Share a redacted excerpt when reporting a problem; do not
upload a personal catalog, commercial samples or plugin binaries.
