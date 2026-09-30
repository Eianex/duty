# DUTY — Download & Upload To YouTube

A small Windows Python application for one YouTube account and channel.
Use a desktop window or Python commands. No API key, AI assistant or hosted service is required.

## First setup

A source clone does not include installed tools. Install **Windows x64 Python 3.11+ with pip** once, then run from this folder:

```text
python src/main.py setup
```

Setup prepares portable Python **3.11.9**, Firefox, GeckoDriver, Deno, FFmpeg,
the existing provider and Python dependencies under `local/`. It reuses installed
components. It does not change global PATH or install a system browser.

After setup, the entry scripts automatically use DUTY's portable Python when
launched with another Python. You can also invoke it directly:

```text
local/runtime/python/python.exe src/gui.py
```

The current source refactor adds PySide6. An existing installation needs setup once
with an installed Python and pip to add this GUI dependency; its other tools are reused.
No ZIP building is involved.

## Desktop window

After setup, double-click **DUTY.exe** in the project root. It finds the local
`pythonw.exe` and `src/gui.py` relative to itself, without a console window.
Background tools run without console popups; Firefox still opens when needed.
You can move the entire installation without rebuilding the launcher.

The small Windows x64 launcher and its C# source are included in the repository.
It uses .NET Framework 4.x, available on Windows 10/11, and embeds `img/logo.ico`.
It contains no installation-specific paths. See the
[launcher build instructions](.agents/docs/project.md#desktop-launcher) to rebuild it.

Or launch through Python:

```text
python src/gui.py
```

The window checks the tools and saved session locally on startup. Download and
Upload open ordinary Firefox if login is needed. Sign in, complete any Google
challenge and select your channel.
DUTY captures cookies automatically. Refresh login is available in the window.
Checks run in order: installed components and provider, account/channel, saved
cookies. Studio is verified only when starting an upload; its status appears after
the upload succeeds. Transfer errors appear only if a transfer needs attention.

Select **Download**, paste a YouTube URL and choose a destination; select
**Upload**, choose an MP4/MKV and enter its title, description, visibility and audience;
or select **Convert** and choose a local MP4 to save an MP3 beside it.
The Download tab has two exclusive buttons: **MP4** (selected by default, video
with audio) and **MP3** (audio only). Video resolution controls are hidden for MP3.
Convert uses local FFmpeg and works without YouTube login or internet access.
The MP4 stays untouched, and the Convert tab will not replace an existing MP3.
Uploads default to **public** and **not made for kids**. The window displays visibility explicitly.

Green means the named check passed; orange means checking, action needed or not
yet verified; red means failure. Tool readiness is not a guarantee against future
YouTube bot checks. One transfer runs at a time.

## Python CLI

```text
python src/main.py login
python src/main.py login --refresh
python src/main.py status
python src/main.py status --online
python src/main.py download "https://www.youtube.com/watch?v=VIDEO_ID"
python src/main.py download "URL" --output "D:\Videos"
python src/main.py download "URL" --format mp4
python src/main.py download "URL" --format mp3
python src/main.py download "URL" --exact-1080
python src/main.py download "URL" --exact-4k
python src/main.py convert "D:\Videos\clip.mp4"
python src/main.py convert "D:\Videos\clip.mp4" --overwrite
python src/main.py upload "D:\Videos\clip.mkv" --title "My video"
python src/main.py upload "D:\Videos\clip.mp4" --visibility private --description "Description"
python src/main.py history
```

Downloads select the best available video and audio and save MP4 without
re-encoding. Exact-resolution options are optional and fail if unavailable.
MP3 downloads select the best available audio and convert it using FFmpeg's
highest MP3 VBR quality setting. Conversion does not improve the source audio.
The separate `convert` command extracts the first audio track from an existing local
MP4 with FFmpeg and writes an adjacent MP3 with the same filename stem. It does
not require a YouTube session. An existing MP3 is protected unless `--overwrite`
is specified. Local conversions do not create YouTube transfer history records.

The default output directory is `local/downloads/`. Repeating identical download
options resumes matching partial files or reuses a verified completed file.

Upload metadata is optional. With no metadata, the title is the filename, the
description is empty and settings supply visibility/audience. An adjacent
`metadata.json` is used when present, or specify `--metadata PATH`:

```json
{"title": "My video", "description": "", "visibility": "public", "made_for_kids": false}
```

Explicit CLI fields override metadata, which overrides `settings.toml`.
`--headless` hides the upload browser; login remains visible. Use `--made-for-kids`
or `--no-made-for-kids` to override audience. Original input files remain untouched.
This version handles single videos only: no batches, search, direct attachment URLs,
scheduling, thumbnails, tags, playlists or editing existing videos.

For automation:

```text
python src/main.py --json --non-interactive download "URL"
python src/main.py --json --non-interactive upload "clip.mkv" --visibility private
```

JSON goes to stdout and progress to stderr. Noninteractive mode returns a login
error instead of opening a login window. Exit codes: **0** success, **1** failure
or unresolved, **2** login required, **130** cancelled before an uncertain upload.

## Python helpers

Use portable Python, or install `requirements.txt` into your Python 3.11 environment.
Put a script in the repository root, or include the root in its Python import path:

```python
from src.core import Config
from src.download import download_video
from src.download import convert_video
from src.upload import upload_video  # run_single_upload remains an alias

config = Config()
config.non_interactive = True
result = download_video("https://www.youtube.com/watch?v=VIDEO_ID", config=config)
print(result.to_dict())

# Convert a local file without accessing YouTube:
# result = convert_video("clip.mp4", config=config)

# Upload only when intended:
# result = upload_video("clip.mkv", config=config, title="My video", visibility="private")
```

Helpers share initialization, locking, authentication and history with the GUI/CLI.
`Config.cancel_event.set()` requests cooperative cancellation; `Config.on_event`
can receive progress/status dictionaries. Embedded operations restore temporary
process environment settings afterward.

## Saved state and recovery

`settings.toml` is the only configuration file. No `.env` is loaded. Everything
private or generated lives under the Git-ignored `local/`, including saved login,
tools, cookies, downloads, history and caches. Move the whole folder to retain
your installation. Do not share `local/` with other users; each channel needs
its own installation.

An upload can become uncertain after attachment. DUTY preserves its ID and refuses
to submit the same file again. The uploader first checks Studio's save signal,
then checks matching metadata for the same video for up to two minutes.
If it remains unresolved, inspect that video in Studio and explicitly reconcile:

```text
python src/main.py history --resolve upload_JOB_ID --status completed --video-id VIDEO_ID
```

Use `--status failed` only after confirming no video was created; it permits a retry.
Cancelling or closing the window after attachment can leave an unresolved upload.

The refactored application, GUI and new completion fallback have **not been
execution-validated**. Existing tests are historical reference, not an active suite.

See [.agents/docs/project.md](.agents/docs/project.md) for architecture, migration
and agent workflows. DUTY-owned code is MIT; third-party code retains its licenses
as described in [THIRD_PARTY.md](THIRD_PARTY.md).

# Third-party components

`LICENSE` applies to DUTY-authored code and the reused code the project owner
has authorized for this distribution. It does not relicense `vendor/`, `local/runtime/`
or installed dependencies. In particular, BgUtils remains GPL-3.0-only, including
DUTY's patches to its files. Its complete vendored source and original GPL license
must accompany the bundle. Bundled Firefox/GeckoDriver, FFmpeg and other components
also retain their own terms. Do not describe the complete portable bundle as
MIT-only or remove upstream notices when making an independent repository.

The transfer implementation includes reused project-owned code.
Upstream files retain their notices. Distribution of third-party
components remains subject to their respective licenses.

| Component | Source / license location |
|---|---|
| CPython 3.11.9 | https://www.python.org/downloads/release/python-3119/ — bundled `local/runtime/python/LICENSE.txt` |
| Firefox | https://archive.mozilla.org/pub/firefox/releases/ — Mozilla notices available in bundled Firefox `about:license` |
| GeckoDriver | https://github.com/mozilla/geckodriver — Mozilla Public License 2.0 |
| Deno | https://github.com/denoland/deno — MIT license |
| FFmpeg | https://github.com/yt-dlp/FFmpeg-Builds — `local/runtime/ffmpeg/LICENSE.txt`, build source tag in `vendor/runtime-sources.json` |
| Python packages | `requirements.txt` pins package versions; installed package metadata includes upstream license files |
| BgUtils PO-token provider 1.3.1 | https://github.com/Brainicism/bgutil-ytdlp-pot-provider — `vendor/bgutil/LICENSE` (GPL-3.0-only), source included |
| Provider dependencies | `vendor/bgutil/server/package-lock.json`; licenses shipped with their packages |

The bootstrap also downloads build-only 7-Zip and Node.js under `local/cache/setup/`;
these are excluded from the portable release. The vendor runtime sources file records binary
download URLs and hashes. BgUtils TypeScript/plugin sources are included, not
replaced by opaque executables. Firefox and FFmpeg source provenance is available
at the versioned upstream URLs referenced above and in the runtime sources file.

DUTY patches the provider in two places: its HTTP provider supports an explicit
disable option, and its Deno command uses cached-only/manual dependency loading
with `--no-lock` to avoid migrating the upstream Deno lock at runtime. The bundled
Node dependency tree is installed from the preserved npm package lock during setup.
This ensures normal use invokes the local script with packaged dependencies.

PySide6 and its Qt/shiboken dependencies retain their upstream licenses and notices in installed package metadata under `local/runtime/python/Lib/site-packages/`. They are not relicensed under DUTY's MIT license.
