# DUTY project knowledge

## Architecture

Six modules own the application. `main.py` dispatches Python commands with lazy
imports, including the offline bundle check. `gui.py` is a PySide6 window
with one worker at a time. `core.py` owns settings, paths, bundled tools, locking, status,
results and history. `session.py` combines the existing Firefox and authentication
code. `download.py` and `upload.py` retain the existing transfer implementations.

The download foundation is yt-dlp, FFmpeg and the vendored BgUtils provider.
The uploader retains the working Selenium/selenium-firefox field and click helpers.
Both frontends call the same helpers; neither frontend performs a separate upload
workflow. Namespace-package imports work from the repository root; there are no
root-level Python wrappers or CMD launchers.

## Bundled dependencies

The repository includes portable CPython 3.11.9 Windows x64 and its pinned
libraries, Firefox, GeckoDriver, Deno, FFmpeg/ffprobe and the installed provider.
Normal users need no system Python, pip, Node, installer or setup command.
The six Python modules and `requirements.txt` remain the source and dependency
pin references; do not silently upgrade components or enable yt-dlp default extras.

Tools and Python site-packages stay at their existing `local/runtime/` paths.
Provider source and upstream locks remain in `vendor/bgutil/`; the installed
runtime copy and native dependencies are in `local/provider/server/`.
Narrow ignore exceptions include these public dependency folders; all other
local data, bytecode and the unused ffplay executable remain ignored.
Unused installed Python console wrappers are also excluded: they reference
the original external installer interpreter and are not used by DUTY.

`vendor/bundle-manifest.json` records file sizes, SHA-256 checksums, package pins
and source provenance. It excludes itself and credentials. The internal
`status --check` verifies file presence/sizes, package metadata and interpreter
identity locally without network, provider execution, login, migration or a lock.
It accepts bundled python.exe/pythonw.exe and checks no saved account state.
Checksums are for source review; startup does not hash the whole installation.

`vendor/deno-cache/npm/` contains reviewed public npm metadata only. Initialization
copies missing seed files to ignored `local/cache/deno/npm/`, keeping existing
cache files. A local manifest fingerprint avoids repeat copying. No provider
session/token cache, compiled code cache or installation archive is published.
All generated data stays under ignored local folders.

Bundled binaries use Git LFS. Clones require Git LFS and `git lfs pull` when
objects are missing. The owner must enable GitHub's **Include Git LFS objects in
archives** setting for working Download ZIP archives; LFS quotas apply.
Before committing/pushing this bundle, the owner runs `git lfs install` inside
the repository to install the LFS upload hook. File rules alone do not install it.
Do not stage, commit, push or configure the user's Git automatically.
Preserve third-party notices and source provenance; the bundle is not MIT-only.

## Desktop launcher

Root `DUTY.exe` is built from `src/launcher.cs`, embeds `img/logo.ico`, and resolves
paths from its own directory. It checks for LFS placeholders in Python executables,
runs hidden `local/runtime/python/python.exe src/main.py --json status --check`,
then starts local pythonw.exe with the quoted GUI path and DUTY's root as cwd.
It uses no command shell, external Python discovery, setup window or dependency
downloads. Missing or incompatible files produce a graphical explanation directing
the user to restore the complete download. The launcher exits after starting Python.

To rebuild from the project root using the Windows .NET Framework compiler:

```powershell
New-Item -ItemType Directory -Force local/cache/launcher-build | Out-Null
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /platform:x64 /optimize+ /debug- /reference:System.Windows.Forms.dll /win32icon:img\logo.ico /out:local\cache\launcher-build\DUTY.exe src\launcher.cs
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed' }
Copy-Item -LiteralPath local/cache/launcher-build/DUTY.exe -Destination DUTY.exe
```

Keep intermediates under ignored local/cache; publish the EXE and source without
debug symbols, personal metadata or machine-specific shortcuts. DUTY.lnk is ignored.
No runtime rebuild or ZIP build is required to prepare the current installed files.

Manual execution validation remains pending: a fresh complete download on a machine
without Python, relocation with spaces/non-ASCII names, offline conversion,
missing/LFS-pointer files and reuse of saved account state. Source review and one
launcher compilation do not establish that these scenarios have passed.

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
environment settings on exit. It uses a standard-library file lock without
third-party lock dependencies. GUI worker events report progress without touching
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
The bundled Python import path is already relative; migration does not rewrite bundled files.

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
