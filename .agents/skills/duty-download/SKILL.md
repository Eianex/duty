---
name: duty-download
description: Download one requested YouTube video using DUTY's saved account, Python CLI and project-local tools.
---

# Download with DUTY

From the repository root, run:

```text
local/runtime/python/python.exe src/main.py --json download "URL"
```

Add `--non-interactive` if you don't want a login window to open. If it comes
back with `login_required`, tell the user. Don't try to make cookies yourself or
switch to another browser.

By default DUTY downloads the best available video and audio as an MP4. Only add
`--output PATH`, `--format mkv|mp3`, `--exact-1080` or `--exact-4k` when the user
asks for them, and don't cap the resolution just because an earlier example did.
It takes one URL at a time (no playlists or batches). Running the same command
again resumes a partial download.

Report the status, local path and any errors exactly as returned. Don't promise
it's immune to bot checks, and don't state the video's actual resolution based
only on the quality flag you passed. Only upload the result if the user asked for
that too, and don't run extra tests, snapshots or check downloads.

If something needs recovering, see [project knowledge](../../docs/project.md).
