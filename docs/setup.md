# Set up Live on macOS

## Python and permissions

Use Python 3.12 or newer. Create a virtual environment and install this repository
as shown in the README. The package installs PyObjC bridges for Accessibility,
Quartz and Cocoa on macOS.

Grant the application running the worker (for example Terminal) Accessibility
access in **System Settings → Privacy & Security → Accessibility**. macOS may also
request Automation permission to control **System Events** when navigating a
native save dialog. Approve that prompt for your chosen terminal. Reopen the
terminal if permissions do not take effect. This does not need Screen Recording.

Open Live once so its preferences and log exist. Keep exactly one Live process
running and save/close unrelated work. The automation must be able to read the
mounted project/sample volumes and write the output directory. It uses `caffeinate`
to inhibit idle sleep while the worker runs; a logged-out or locked desktop is
not a supported unattended environment.

## AbletonOSC

Install [AbletonOSC](https://github.com/ideoforms/AbletonOSC#installation) as a
third-party Remote Script. On a default macOS User Library, its folder is:

```text
~/Music/Ableton/User Library/Remote Scripts/AbletonOSC/
```

If you moved the User Library, use its actual location. Follow the upstream
instructions to select **AbletonOSC** as a Control Surface in Live's MIDI
preferences and restart Live. Default OSC request/reply ports are **11000/11001**.
Only one exporter or other query client may bind the reply port at a time.

### Required Session property

The Session recorder needs `/live/song/get/start_time` and
`/live/song/set/start_time`. The upstream
[song handler](https://github.com/ideoforms/AbletonOSC/blob/master/abletonosc/song.py)
checked when packaging this project did not expose that property. A helper adds
`start_time` to its read/write property list without bundling a fork.

Quit Live and preview the change first:

```sh
python scripts/patch_abletonosc.py \
  "$HOME/Music/Ableton/User Library/Remote Scripts/AbletonOSC"
```

Then apply and restart Live:

```sh
python scripts/patch_abletonosc.py \
  "$HOME/Music/Ableton/User Library/Remote Scripts/AbletonOSC" --write
```

The helper preserves `song.py.before-batch-export`, does nothing if already patched,
and refuses unfamiliar source layouts. To undo, quit Live and restore that backup.
Use the same procedure after an upstream upgrade if the handlers disappear.
`ableton-export doctor --live` reads the handler to check availability without
starting playback or changing the open set.

## Set export preferences once

In a disposable set, open **File → Export Audio/Video…**, choose the following,
then cancel the dialog. Live remembers these choices. The automation verifies
them each time; it edits only the start and length fields itself.

| Setting | Value |
|---|---|
| Rendered Track | Main |
| Render as Loop | Off |
| Convert to Mono | Off |
| Normalize | Off |
| Create Analysis File | Off |
| Sample Rate | 44100, or the configured sample rate |
| Encode PCM | On |
| File Type | WAV |
| Bit Depth | 16 |
| Encode MP3 | Off |
| Create Video | Off |

Dither is left at the setting you choose in Live; it is not currently verified by
the driver. The original batch used Triangular. Export dialog labels and
Accessibility identifiers are version-sensitive and currently assume English.

After configuration, run `doctor --live`, then a one-set export and listen to it.
An OSC response alone does not validate the full export/save UI on your version.
