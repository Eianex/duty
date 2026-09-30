# Working on DUTY

DUTY is a standalone Windows Python app that downloads YouTube videos, uploads
videos to one channel and converts MP4s to MP3s. It has a PySide6 desktop window
and a CLI, and both call the same code. Always work from the current code rather
than older notes or the legacy tests.

For the details, read:

- [Project knowledge](.agents/docs/project.md): architecture, setup, the launcher and transfer behaviour
- [Setup skill](.agents/skills/duty-setup/SKILL.md)
- [Download skill](.agents/skills/duty-download/SKILL.md)
- [Upload skill](.agents/skills/duty-upload/SKILL.md)

## Where things go

- All application code lives in the six files in `src/`. Commands go in
  `src/main.py`, and the desktop window lives in `src/gui.py`. Please don't add
  new modules or root-level wrapper scripts.
- Installed tools and anything private or generated (logins, cookies,
  downloads, history, caches) belong under `local/`, which Git ignores.
- `DUTY.exe` in the root is built from `src/launcher.cs`. The build steps are in
  project knowledge.
- There's one `settings.toml` and one `requirements.txt`. Keep it that way.
- Leave the third-party licenses in `vendor/` and [THIRD_PARTY.md](THIRD_PARTY.md)
  intact, and don't disturb the saved login or transfer history in `local/`.

## What not to run

Transfers touch a real YouTube account, so treat running things as something the
user opts into:

- Don't add or run tests, browser snapshots, live "validation" transfers,
  runtime rebuilds or ZIP builds.
- Review the source instead of running the app, unless the user tells you
  otherwise.
- If the user asks you to download or upload something, do exactly that. It's
  not permission to run extra transfers to check your work.

## Uploads and login

- Uploads are public by default. Use whatever visibility the user asks for, and
  don't quietly switch it to private.
- If an upload ends up unresolved, never attach that file again, and never find
  a way around the configured channel. Ask the user to check Studio and reconcile
  it (see the upload skill).
- Never store passwords in the source or in settings.
- Setup downloads its own dependencies, so skills should just run the app. When
  a login is needed, explain that the user signs in themselves in the Firefox
  window DUTY opens.
