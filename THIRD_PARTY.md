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
