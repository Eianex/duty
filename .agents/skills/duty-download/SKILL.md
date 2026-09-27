---
name: duty-download
description: Download one requested YouTube video using DUTY's saved account, Python CLI and project-local tools.
---

# Download with DUTY

Use `local/runtime/python/python.exe src/main.py --json download "URL"` from the
repository root. Use `--non-interactive` when no login window is desired; report
login_required rather than manufacturing cookies or substituting another browser.

Default is best available video and audio in MKV. Add `--output PATH`, `--format mp4`,
`--exact-1080` or `--exact-4k` only when requested. Do not impose a resolution cap
because a previous example used one. This command accepts one URL, not a playlist
or batch. Repeating matching options resumes supported partial downloads.

Report the returned status, local path and errors honestly. Do not claim bot
immunity or actual media dimensions based only on a requested quality flag.
Do not upload the result unless the user requested uploading too. No extra tests,
browser snapshots or validation transfers are part of this skill.
See [project knowledge](../../docs/project.md) if recovery is needed.
