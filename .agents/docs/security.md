# Bundled dependencies and GitHub secret alerts

## Review of the 2026-10-12 alerts

GitHub reported 11 alerts after the dependency bundle was published in commit
`e2564a0b290258d459972789d57e830abb7794ee`. They cover 19 distinct files, including
duplicate constants in JavaScript declarations, bundles and source maps.

The reported values are service/API credentials embedded in upstream components,
not credentials entered by a DUTY user. GitHub marked all 11 as publicly leaked
with validity **unknown**. This review does not declare those values revoked,
inactive, fake or safe for other uses.

| Alerts | Component | Evidence |
|---|---|---|
| 1, 2, 3, 5, 6, 8, 9, 10 | yt-dlp 2026.8.19 | All five flagged extractor files match the checksum-verified official PyPI wheel byte for byte. Includes the Shahid AWS credentials. |
| 7 | youtubei.js 16.0.1 | All eight flagged files match the published npm archive byte for byte. |
| 11 | bgutils-js 3.2.0 | All five flagged files match the published npm archive byte for byte. |
| 4 | Firefox 152.0.5 | The alert literal matches `MOZ_GOOGLE_SAFEBROWSING_API_KEY` in `modules/AppConstants.sys.mjs` inside omni.ja. Firefox's executable has a valid Mozilla Authenticode signature. |

Firefox's evidence identifies its built-in Safe Browsing constant; it does not
claim a byte-for-byte comparison of the complete omni.ja against a downloaded
Mozilla installer. The reviewed files match DUTY's existing bundle manifest.
No account profiles, cookies, transfer history, downloads or working caches were
tracked in the current repository.

The decision is to retain these upstream files unchanged and resolve only these
reviewed alerts using GitHub's **Won't fix** classification, with provenance
comments. This is an explicit third-party dependency decision, not a declaration
that valid credentials are false positives. Their rotation is not controlled by
DUTY's owner. No credentials were exercised to check validity.

See [the review record](secret-scanning-review.json) for file hashes, upstream
artifact URLs and GitHub resolution receipts. The record contains no key values.

## Handling future alerts

Keep GitHub secret scanning enabled. There are no blanket path exclusions for
dependencies, runtimes, source code or local data. A new alert must be reviewed
on its own; do not dismiss it because it occurs in a bundled folder.

Compare the flagged file with the exact official package/version and inspect its
role without printing literal secrets. Resolve a verified retained upstream
credential with an accurate explanation. Do not label it revoked unless it was
actually revoked, and do not edit or encode key strings merely to evade scanning.

If a value belongs to a DUTY user or was added locally, keep the alert open until
the owner has revoked/rotated it and the exposure has been addressed. Removing a
file in a new commit does not remove it from published history. History rewriting
and pushing require explicit user authorization; never perform them automatically.

Upstream file changes require reviewing the bundle manifest and this evidence
again. Preserve the existing private-data ignore rules and upstream licenses.
