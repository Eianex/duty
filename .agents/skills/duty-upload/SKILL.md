---
name: duty-upload
description: Upload one user-requested local MP4 or MKV through DUTY's standalone uploader, preserving visibility, channel binding and duplicate protection.
---

# Upload with DUTY

Only upload when the user has asked for it. This skill doesn't cover test
uploads. From the repository root, run:

```text
local/runtime/python/python.exe src/main.py --json upload "VIDEO"
```

The input has to be a local MP4 or MKV, not a link or an attachment from
somewhere else.

Uploads are public and not made for kids by default. Use the title, description,
visibility and audience the user asks for, either as CLI options or in a
metadata JSON file. Don't switch a normal upload to private on your own. If a
`metadata.json` sits next to the video, DUTY uses it, and CLI options override it.

DUTY uploads through its own saved Firefox session and configured channel.
There's no other upload path. Only tell the user the upload succeeded when the
result says it completed.

## If the upload is unresolved

Keep the job ID and URL that were recorded and stop there. **Never attach the
file again.** Ask the user to look in YouTube Studio. Once they've confirmed what
happened, reconcile it:

```text
local/runtime/python/python.exe src/main.py history --resolve JOB_ID --status completed --video-id VIDEO_ID
```

Only use `--status failed` once the user has confirmed that no video was created.
That's what allows a retry.

No extra uploads, tests, snapshots or packaging. For how completion checks and
login work, see [project knowledge](../../docs/project.md).
