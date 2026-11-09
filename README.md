# Ableton Batch Export

Export a folder of Ableton Live sets to organized stereo WAVs on macOS. The tool
indexes `.als` files, relinks available samples in **working copies**, drives Live's
native export dialog, and records a receipt for every completed render.

It grew out of a real back-catalog export. It is an experimental desktop automation
tool: Live must be installed, licensed, running in a logged-in macOS session, and
configured as described below. No AI service, chat application, API key, Node.js,
or paid automation service is required.

## What it does

- Finds main sets recursively; excludes folders named `Backup`, `Backups`, and
  `abletoolz_backup` from the render queue.
- Resolves sample references using existing paths, names, byte sizes, matching
  path suffixes and, when candidates tie, content hashes. Ambiguous references are
  reported rather than chosen arbitrarily.
- Writes relinked copies under the output folder. Hashes original sets before and
  after rendering and during finalization.
- Exports Arrangement content, with an optional three-pass performance of every
  occupied scene in **Session-only** sets.
- Saves WAVs in folders mirroring the source tree, with readable names and stable
  ID suffixes when names collide.
- Resumes from completion receipts, logs missing plugins, and bounds stalled
  loads, Session playback, and renders.
- Creates an M3U8 playlist plus `Export to Source.csv` and
  `Unexported Projects.csv`, including exclusions and failure reasons.

## Supported setup and limits

The original native workflow was exercised with **Ableton Live 12.4.7 Standard**, an
English UI, macOS, and Python 3.12. Other Live versions, editions and languages
have not been validated. The extraction into this repository is covered by offline
tests; those tests do not establish compatibility with every Live installation.

The renderer currently supports **4/4, stereo, 16-bit PCM WAV**. The example uses
44.1 kHz and an eight-beat tail, with ranges rounded to full bars. It exports the
Main output; it does not export stems. External Instrument / External Audio Effect
sets are excluded. Plugins, instruments and samples that are unavailable cannot
be reconstructed; a successful WAV is not a guarantee of the original sound.

Session performances are auditions, not recovered arrangements: each occupied
scene runs three times, top to bottom, for its longest clip rounded up to a bar.
Follow actions, clip legato and non-trigger launch modes are disabled in the working
copy. Tracks are disarmed. Scene tempo is honored; non-4/4 scenes are rejected.
Sets with both Arrangement and Session content export the Arrangement only.

## Quick start

1. Install **Python 3.12+** and Ableton Live. Complete the
   [Live and AbletonOSC setup](docs/setup.md), including the small `start_time`
   patch and initial export settings.
2. In this repository:

   ```sh
   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install -e .
   ```

3. Create a private configuration. Replace these example paths with your own;
   output and local state must be outside the original project tree:

   ```sh
   ableton-export init \
     --project-root "/Volumes/Music/Projects" \
     --sample-root "/Volumes/Music" \
     --output "/Volumes/Music/Batch Renders" \
     --unattended
   ```

   `--unattended` permits Live to come forward for opening sets and export/save
   dialogs, while Session playback continues in the background. Omit it to wait
   for Live to be foreground. Save any unrelated work before starting a batch.
   `config.json`, local state, media and generated reports are ignored by Git.

4. Open Live, then check the setup and index your files:

   ```sh
   ableton-export doctor --live
   ableton-export index
   ableton-export audio-index
   ```

5. Preview the queue, inspect `reports/preparation-preview.json` under your output
   folder, then create the working copies:

   ```sh
   ableton-export prepare --include-session
   ableton-export prepare --include-session --write
   ```

   Unresolved media is excluded by default. To deliberately audition partial audio,
   add **`--allow-missing-media` to both preparation commands**. Such WAVs get
   `[missing media]` in their names and a `partial_missing_media` status. Empty sets,
   hardware-dependent sets and disabled Session exports remain explicitly excluded.

6. Try one set in the foreground, then run the rest:

   ```sh
   ableton-export export --limit 1
   ableton-export verify
   # Listen to that WAV and inspect its receipt before proceeding.
   ableton-export export --detach
   ableton-export status
   ```

   Logs are in `run-state/batch.log`; live state is in `run-state/batch.json`.
   Export settings must already match the configured values. Unexpected settings
   or dialogs stop the batch for inspection rather than guessing.

7. Once the worker exits, or after stopping it, verify and build the deliverables:

   ```sh
   ableton-export finalize
   ```

   Finalization requires the source and output volumes to be mounted. It reads
   each completed WAV, verifies original hashes, refreshes reports and the playlist,
   and writes both CSVs. Pending, skipped and failed sets stay unexported.

## Stop, resume, and skip

```sh
ableton-export stop
ableton-export status
# Wait for step=stopped and for the process to exit.
ableton-export finalize
```

The worker checks the stop marker, stops its working-copy Session transport, and
attempts to cancel owned export/save dialogs. A partially written WAV is preserved.
If a cleanup warning appears, inspect Live before resuming.

Run `ableton-export export --detach` again to resume. Completed WAVs with receipts
are not overwritten. Pending work precedes previous failures; each failed set gets
at most one attempt per invocation. An existing WAV without a valid completion
receipt is preserved and reported, never silently replaced.

To exclude a troublesome pending job, stop the worker first and use its ID from
`queue.json` or the logs:

```sh
ableton-export skip SET_ID --reason "Needs manual plugin repair"
ableton-export export --detach
```

Do not run `prepare --write` again to resume: an existing queue is deliberately
protected. For a new preparation policy, use a separate output folder, config file,
state database and runtime directory. See [configuration and operation](docs/operation.md).

## Output layout

```text
Batch Renders/
├── wav/
│   └── Example Project/
│       ├── Example.wav
│       └── Old Sketch [missing media].wav
├── working/v1/                 # Relinked disposable sets + provenance
├── reports/                    # Status, verification, source hashes, receipts
├── queue.json                  # Authoritative source → working copy → WAV mapping
├── Back Catalog.m3u8           # Relative paths; keep alongside wav/
├── Export to Source.csv
└── Unexported Projects.csv
```

With multiple project roots, each gets a distinct root-name/ID prefix. CSV paths
are absolute for traceability; the playlist uses paths relative to the output
folder. CSV columns retain plugin references, runtime warnings and partial-media
notes. `near_silent` means peak amplitude below -80 dBFS, not a missing file.

## Development

```sh
python -m unittest discover -s tests -v
python -m pip install build
python -m build
```

Tests use generated temporary fixtures and mocks; no project files, sample packs,
Live launch or external volume are required. See [architecture](docs/architecture.md),
[troubleshooting](docs/troubleshooting.md), and [contribution notes](CONTRIBUTING.md).

To publish your own copy, follow [the GitHub checklist](docs/publishing.md).
No personal catalog, logs, databases, render receipts, media or third-party
application binaries are included in this repository.

## License and acknowledgments

GPL-3.0-only; see [LICENSE](LICENSE). The project uses
[abletoolz](https://github.com/elixirbeats/abletoolz) for Live set parsing and
[AbletonOSC](https://github.com/ideoforms/AbletonOSC) for Live's control interface.
See [THIRD_PARTY.md](THIRD_PARTY.md). Ableton Live is a separate proprietary
application. This is an independent project, not an Ableton product.
