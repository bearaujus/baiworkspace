# baiworkspace

**Your workspace. Your agent. Your context.**

A personal work hub for AI agents: persistent knowledge, connected repository
maps, task handoffs, reusable scripts, private credential entry and encrypted backups.

Clone -> open your preferred agent here -> start talking.

```sh
git clone https://github.com/bearaujus/baiworkspace.git
cd baiworkspace
# Launch your installed agent here, or open this folder in its editor.
```

You need **Python 3.11+** and an agent with local file and command access. Startup
files are included for Codex, Claude Code, Gemini CLI, Cursor and Copilot. For
another agent, ask it to read `AGENTS.md`. Its normal permissions still apply.
No Python packages, server, account connection or global agent configuration is
needed. [age](https://github.com/FiloSottile/age) is required only for backups.

## Just talk

- “I work on these two repos. Here is how they connect.”
- “Investigate this failure. Check whether we already have a script.”
- “Continue the task from yesterday.”
- “That flow changed. Update what you remember.”
- “Review our memories for outdated assumptions.”

The agent initializes local storage, retrieves relevant context, inspects current
evidence, reuses tools and checkpoints meaningful work. You supply intent and
corrections. Small questions stay small.

Personal data is stored in **Git-ignored `.bai/`**. Framework updates stay separate
from your tasks, repository bindings, knowledge, custom scripts and credentials.
Application repositories can live anywhere; registering one does not modify it.

## Credentials without pasting into chat

The agent prepares a script and declares its required keys. It then gives you a
command to run in your own terminal:

```sh
make secrets-setup TOOL=demo-service-check PROFILE=dev
make secrets-list PROFILE=dev
make secret-set KEY=DEMO_SERVICE_TOKEN PROFILE=dev
make secret-remove KEY=DEMO_SERVICE_TOKEN PROFILE=dev
```

The included demo makes no network calls. Enter a dummy value to try the flow.
Input is hidden; listing shows key names and status. The runner injects only
declared keys for the selected profile and redacts their known values from output.
The local credential store is plaintext, ignored by Git, and under your control.
It does not isolate secrets from an unrestricted local process.

**Make is optional.** On native Windows, use:

```powershell
py -3 bai.py init
py -3 bai.py secrets setup --tool demo-service-check --profile dev
py -3 bai.py tools run demo-service-check --profile dev
```

On macOS/Linux the equivalent prefix is `python3 bai.py`.

## Backup and recovery

```sh
make backup
make restore-backup FILE=path/to/workspace-backup.age
```

Run these yourself in a terminal. Backups include credentials and use age
passphrase encryption. Keep the passphrase separately; it cannot be recovered
from the workspace. Restore into a fresh clone without `.bai/`; existing personal
data is never overwritten. External application repos are not included.

## Useful commands

```sh
python3 bai.py init
python3 bai.py doctor
python3 bai.py status
python3 bai.py context --query "service request flow" --json
python3 bai.py tools find "CSV"
python3 bai.py memory review
python3 bai.py --help
```

Commands default to this clone even when called from another working directory.
Use `--root /path/to/clone` before a command to select another workspace.
`--json` is supported before or after subcommands. Tool arguments go after `--`.

Read [daily use](docs/daily-use.md), [connected memory](docs/memory.md),
[tool contracts](docs/tools.md), [credentials](docs/credentials.md),
[backup recovery](docs/backup.md), and [contributing](CONTRIBUTING.md).

Licensed under [MIT](LICENSE).
