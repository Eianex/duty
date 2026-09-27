---
name: duty-setup
description: Set up this DUTY installation and guide its owner through standalone Firefox login when they request setup or authentication repair.
---

# Set up DUTY

Use the repository's Python application. A fresh clone requires Windows x64
Python 3.11+ with pip; do not invent an agent-managed download or browser workflow.
Run `python src/main.py setup` when setup is requested. It installs missing tools
and packages automatically; it preserves existing local tools and saved state.
Portable Python 3.11.9 is downloaded by setup, not shipped in Git. External pip
uses `--python` to install through this interpreter, so dependency conditions
and wheels match 3.11.9. Pip must be 22.3+ with Python 3.11 support; report setup's
errors rather than silently updating external pip or changing dependency pins.

After setup use `local/runtime/python/python.exe src/main.py login`. Tell the user
to complete Google login, two-factor prompts and channel selection in the ordinary
Firefox window. The application saves its own session. Never ask for passwords
or claim an unrelated browser session authenticates DUTY.

For a requested authentication refresh use `login --refresh`; the configured
channel must match. Missing packages require setup with the initial installed
Python/pip. Network/provider errors do not justify repeated login windows.

No live download/upload is implied by setup. Do not run tests or build ZIPs.
See [project knowledge](../../docs/project.md) for component roles and recovery.
