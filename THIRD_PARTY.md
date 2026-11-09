# Third-party components

This repository contains the exporter and an original helper that edits a locally
installed AbletonOSC property list. It does not vendor the dependencies below.

| Component | Use | Upstream license |
|---|---|---|
| [abletoolz](https://github.com/elixirbeats/abletoolz) 2.1.0 | Live set/sample/plugin reference parsing | GNU GPL v3 |
| [AbletonOSC](https://github.com/ideoforms/AbletonOSC) | Separate Live Remote Script, installed by the user | MIT |
| [PyObjC](https://github.com/ronaldoussoren/pyobjc) | macOS framework bindings | MIT |
| [NumPy](https://github.com/numpy/numpy) | PCM audio measurements | BSD-3-Clause |

Python package dependencies and their transitive dependencies are installed from
their own distributions. Consult those distributions for their license notices.
AbletonOSC remains a separate installation; the patch helper preserves a local
backup and adds only the required `start_time` property. Its upstream license
notices remain in that installation.

The exporter is released under GPL-3.0-only. `LICENSE` contains the standard GPL v3
text. Ableton Live, third-party plugins, presets, packs and user media are separate
and are not included or licensed by this project. Ableton is a trademark of its
respective owner; there is no affiliation or endorsement.
