# Working on DUTY

Use the current code. DUTY is a standalone Windows Python application.

- [Project knowledge](.agents/docs/project.md)
- [Setup skill](.agents/skills/duty-setup/SKILL.md)
- [Download skill](.agents/skills/duty-download/SKILL.md)
- [Upload skill](.agents/skills/duty-upload/SKILL.md)

Application code belongs in the six files in `src/`. Use `src/main.py` for commands
and `src/gui.py` for the desktop window. All installed tools and private/generated
data belong under `local/`. The root `DUTY.exe` launcher has its source in
`src/launcher.cs`; its build instructions are in project knowledge.
One `settings.toml` and one `requirements.txt` are used.
Preserve third-party licenses and existing authentication/history.

Do not add or run tests, browser snapshots, live validation transfers, runtime
rebuilds or ZIP builds. Review source without executing the application unless
the user explicitly changes these instructions. Ordinary transfers explicitly
requested by the user are application use, not permission for extra validation.

Uploads default to public; use the requested visibility. Never reattach an
unresolved upload or bypass the configured channel. Do not store passwords in
source or settings. Setup downloads dependencies itself; agent skills only invoke
the application and explain the user's Firefox login step.
