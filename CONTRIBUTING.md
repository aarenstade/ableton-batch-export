# Contributing

Keep the tool small and explicit. New integrations should not require private
automation services. Preserve source `.als` files, existing completed audio and
durable receipts. Include a reason when excluding a main set.

Install with `python -m pip install -e .`, then run
`python -m unittest discover -s tests -v`. Use synthetic XML and generated PCM
fixtures in temporary directories. Do not commit real projects, media or logs.

For UI changes, report the Live edition/version, macOS version and UI language,
then describe a native smoke test using your own disposable set. Offline tests
cannot validate Accessibility identifiers. Never expand recognized-dialog handling
to indiscriminately dismiss or save unknown dialogs.

For bug reports, provide the failing phase, redacted error, configuration values
relevant to that phase, and whether playback/file-size progress was visible. State
whether a job had missing media or plugins. Do not include license keys or media.

Contributions are provided under this repository's GPL-3.0-only license.
