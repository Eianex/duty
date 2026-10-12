---
name: duty-setup
description: Check DUTY's bundled installation and guide its owner through standalone Firefox login when they request first use or authentication repair.
---

# Prepare to use DUTY

DUTY includes Python, tools and dependencies; there is no installation setup.
When the user requests setup or environment repair, inspect readiness from the root:

```text
local/runtime/python/python.exe src/main.py --json status --check
```

This check is offline and ignores login. If bundled Python itself is missing,
restore the complete download. For missing Git LFS binaries in a clone, explain
`git lfs pull`. Do not install system Python, pip or packages, change pins, or
create another download/browser workflow.

## Signing in

When the user requests login, run:

```text
local/runtime/python/python.exe src/main.py login
```

Explain that the user signs in themselves in the Firefox window DUTY opens,
completes verification and selects their channel. DUTY saves the session.
Never ask for or store their password. Use `login --refresh` only when requested;
the configured channel still applies. Network/provider failures are not a reason
to repeatedly open login windows.

Environment repair does not authorize transfers, tests or ZIP builds.
See [project knowledge](../../docs/project.md) for recovery and architecture.
