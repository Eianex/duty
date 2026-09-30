# DUTY project knowledge

## Architecture

Six modules own the application. `main.py` dispatches Python commands with lazy
imports so setup works before dependencies exist. `gui.py` is a PySide6 window
with one worker at a time. `core.py` owns settings, paths, setup, locking, status,
results and history. `session.py` combines the existing Firefox and authentication
code. `download.py` and `upload.py` retain the existing transfer implementations.

The download foundation is yt-dlp, FFmpeg and the vendored BgUtils provider.
The uploader retains the working Selenium/selenium-firefox field and click helpers.
Both frontends call the same helpers; neither frontend performs a separate upload
workflow. Namespace-package imports work from the repository root; there are no
root-level Python wrappers or CMD launchers.

## Setup and dependencies

A fresh Git clone needs external Windows x64 Python 3.11+ with pip to run
`python src/main.py setup`. Setup uses the pinned URLs/checksums in
`vendor/runtime-sources.json`; Python package versions are in `requirements.txt`
without hashes. PySide6 is an added GUI dependency. Do not silently upgrade tools.

Setup installs missing components in staging folders beneath `local/cache/setup/`
and promotes them into `local/runtime/`. Existing executable components are reused.
Incomplete destinations are reported rather than overwritten. Failed staging
files remain available for diagnosis. Portable CPython 3.11.9 Windows x64 is
downloaded first and stays excluded from Git with all other local tools.
External pip uses `--python` to run dependency installation through the portable
interpreter, evaluating wheel compatibility and dependency markers under 3.11.9.
This needs pip 22.3+ with Python 3.11 support; do not silently upgrade external pip
or add pip to portable Python. Install the complete requirements when packages
are missing, keeping every pin and using wheels only. Do not enable extra groups
such as yt-dlp's default extra, which conflicts with the existing urllib3 pin.
Packages are installed in the portable interpreter's local site-packages.
The portable interpreter deliberately does not need a system Python after setup.
Setup directs users to the repository's root `DUTY.exe` launcher. It does not
create shortcuts or compile the launcher during installation.

Provider source and upstream locks remain in `vendor/bgutil/`. A runtime copy of
the TypeScript server and its native dependencies lives in `local/provider/server/`.
Setup uses project-local Node/npm to install this copy; normal downloads use Deno.
Caches, npm downloads and staging data remain beneath `local/`.

There is no packaging command or hosted service. A source clone excludes tools and
credentials. Moving an existing whole installation retains them; sharing one
signed-in installation would also share its account and must be avoided.

## Desktop launcher

`src/launcher.cs` is a small .NET Framework Windows GUI launcher, separate from
the six Python modules. Root `DUTY.exe` embeds `img/logo.ico`, finds its own
directory, and starts local pythonw with the quoted GUI path and that directory
as its working directory. It uses no command shell, stored personal paths or
script associations. Missing files and process-start errors use message boxes.
It exits after starting Python; normal users do not need a compiler.

Before starting the GUI, the launcher runs the hidden `setup --check` with local
Python. This check is offline and ignores login. If local Python is missing or the
check fails, it finds an external Windows x64 Python 3.11+ with pip 22.3+ through
`py -0p` and PATH, skipping the portable interpreter. It then runs `setup
--launcher-progress` hidden inside a WinForms progress window, checks again and
starts the GUI. Output is appended to `local/logs/launcher-setup.log`. Without a
suitable interpreter or after a failure, the window shows the error and does not
start the GUI.

To rebuild from the project root using the Windows .NET Framework compiler:

```powershell
New-Item -ItemType Directory -Force local/cache/launcher-build | Out-Null
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /platform:x64 /optimize+ /debug- /reference:System.Windows.Forms.dll /win32icon:img\logo.ico /out:local\cache\launcher-build\DUTY.exe src\launcher.cs
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed' }
Copy-Item -LiteralPath local/cache/launcher-build/DUTY.exe -Destination DUTY.exe
```

Keep build intermediates under ignored `local/`; include the final EXE and source
in the repository. Do not add debug symbols, personal assembly metadata or
machine-specific shortcuts. Root `DUTY.lnk` is ignored and no longer generated.
Deleting the old shortcut does not remove it from earlier Git commits; publication
history cleanup is a separate user-controlled action.

Execution validation is pending. Manual acceptance scenarios: double-click shows
only the GUI; moving the folder (including spaces/non-ASCII characters) still
works; missing setup files show a graphical explanation; background Deno, FFprobe
and GeckoDriver windows remain suppressed. Do not run these checks without user
authorization. Launcher compilation and static binary inspection are distinct
from runtime rebuilds or distribution ZIP builds.

## Session and operation invariants

One installation is bound to one channel. Login opens ordinary Firefox; the user
completes Google sign-in and channel selection. GeckoDriver verifies Studio only
when an upload starts, before attaching its file. Startup checks all installed
components (including the provider), then the account and saved cookies; it does
not open a browser to verify Studio. The GUI shows Studio status only after a
successful new upload, and transfer errors only when a transfer fails or remains
unresolved. A new saved profile/cookie generation becomes active only
after successful capture. Failed refreshes retain the last saved generation.

Downloads get disposable cookie files. Browser operations use disposable working
profiles. Cleanup runs on failure/cancellation too. Cookie-copy protection prevents
local overwrites; it cannot prevent server-side expiry or rotation.

The shared operation context provides a process/thread lock and restores temporary
environment settings on exit. It uses a standard-library file lock even before
dependencies are installed. GUI worker events report progress without touching
widgets off the UI thread. Cancellation is cooperative and may wait for the
current browser/network operation or FFmpeg processing to return.

## Transfer behavior

Download defaults: best video plus best audio, MP4, no resolution or codec cap.
The GUI offers exclusive MP4/MP3 buttons. MP3 selects the best available audio
and converts it with FFmpeg at quality 0; MKV remains an explicit CLI/helper option.
MP4 and exact 1080p/2160p are explicit options. Validate actual output streams;
resolve only yt-dlp-reported outputs. Cross-drive finalization preserves originals
until copy verification succeeds. Filename collisions never overwrite existing files.

The Convert tab and `convert` CLI command extract the first audio track of a local
MP4 into an adjacent MP3 using local FFmpeg and ffprobe. Conversion reuses the
operation lock and progress/cancellation interface but needs no cookies, provider,
Firefox or network. The GUI's initial status check is local; download/upload
authentication begins when a YouTube transfer needs it. Existing MP3s are kept
unless the CLI explicitly requests `--overwrite`. Conversion has no YouTube
transfer history record.

Upload defaults: public, not made for kids, visible Firefox, local MP4/MKV input.
Metadata is optional; explicit fields override metadata and then settings. Invalid
input fails before launching Firefox. Journal attachment before file submission.
Deduplicate by file contents and channel. Preserve the existing Result fields,
including `stop_batch` for callers/history compatibility, although batches are gone.

After Save, first observe Studio completion. If that times out, poll authenticated
metadata for only the recorded video ID, at ten-second intervals for at most two
minutes. Require ID, channel, title, description and visibility to match. Each
read is bounded in a disposable child process. No authentication refresh, upload
retry or publishing action occurs inside this fallback. Unknown outcomes remain
unresolved. Cancel after attachment also remains unresolved.

The original live checks successfully downloaded and privately uploaded a video;
Studio completion reporting returned a false timeout. That historical success
does not validate this refactor or the new fallback. Do not describe the GUI,
MKV upload changes or refactored application as execution-validated.

## Layout migration

Old root runtime/permanent/work/downloads/history/logs/cache/dist folders move
under `local/`; provider node_modules move to the local server. No executable
rebuild is needed. Migration preflights conflicting destinations, moves complete
directories and can resume after interruption. It never merges conflicting state.
The embedded Python import path follows the new location.

Only application-owned `local_path` history fields beneath the old downloads/work
directories are relocated. Fingerprints, channel identity and cookie generations
are retained. Browser databases and arbitrary historic log text are not rewritten.
Unmatched old partial jobs are preserved for recovery; requests with changed
paths/options may start a new job rather than reusing an uncertain partial.
Historical batch/Codex journals stay readable, but those command paths are removed.

Existing tests are retained under `.agents/legacy-tests/` unchanged as historical
reference. Their imports target the former structure; do not run them or claim
they verify the new one. No new tests or application execution accompanied this
refactor. Review current source and documentation only unless instructed otherwise.
