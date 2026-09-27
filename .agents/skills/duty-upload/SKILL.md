---
name: duty-upload
description: Upload one user-requested local MP4 or MKV through DUTY's standalone uploader, preserving visibility, channel binding and duplicate protection.
---

# Upload with DUTY

Use `local/runtime/python/python.exe src/main.py --json upload "VIDEO"` from the
repository root. The user must have requested the upload; this skill does not
authorize additional test uploads. Input is a local MP4/MKV, not a remote attachment.

Defaults are public and not made for kids. Respect requested title, description,
visibility and audience using the CLI options or an optional metadata JSON file.
Do not silently force private visibility for ordinary requested uploads. An
adjacent metadata.json is used when present; explicit CLI values take precedence.

Use DUTY's saved Firefox session and configured channel. No Codex-browser upload
path exists. Report successful completion only when the returned result confirms
it. If unresolved, preserve the recorded ID/URL and stop: never attach the file again.

After the user confirms the outcome in Studio, reconcile with
`history --resolve JOB_ID --status completed --video-id VIDEO_ID`. Mark failed only
after confirming no video was created; that permits a subsequent retry.

No extra uploads, tests, snapshots or packaging. See
[project knowledge](../../docs/project.md) for completion and authentication behavior.
