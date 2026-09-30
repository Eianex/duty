---
name: duty-setup
description: Set up this DUTY installation and guide its owner through standalone Firefox login when they request setup or authentication repair.
---

# Set up DUTY

A fresh clone needs Windows x64 Python 3.11+ with pip, and that's all. When the
user asks for setup, run this from the repository root:

```text
python src/main.py setup
```

Setup takes care of the rest itself. It downloads portable Python 3.11.9 (that's
not in Git), the tools and the Python packages. Whatever is already installed,
including the saved login, is left alone. Don't build your own download or
browser workflow around it.

Setup installs packages with the external pip, using `--python` to point it at the
portable interpreter, so wheels and dependency markers match 3.11.9. That needs
pip 22.3+ with Python 3.11 support. If setup reports a pip or dependency error,
pass it on to the user. Don't quietly upgrade pip or change the pinned versions.

## Signing in

After setup, run:

```text
local/runtime/python/python.exe src/main.py login
```

Tell the user that a normal Firefox window will open, and that they sign in to
Google there, get through any two-factor prompts and pick their channel. DUTY
saves the session on its own. Never ask for their password, and don't suggest
that being signed in in some other browser counts.

If they ask to refresh the login, use `login --refresh`. It has to be the same
channel as before. Missing packages mean setup has to run again with the
installed Python and pip. A network or provider error isn't a reason to keep
opening login windows.

Setup doesn't mean a download or upload should happen, and it doesn't cover
running tests or building ZIPs. For how the pieces fit together and how to
recover, see [project knowledge](../../docs/project.md).
