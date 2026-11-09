# Encrypted backup and restore

Install [age](https://github.com/FiloSottile/age) separately. Its official binaries
support macOS, Linux and Windows. Examples: `brew install age` on Homebrew systems,
or `winget install --id FiloSottile.age` on Windows. Use your platform's package
manager or verify an official binary. The workspace does not install it for you.

Run backup in your own terminal:

```sh
make backup
# Optional explicit output (existing parent, new filename):
python3 bai.py backup create --output /path/to/private-backups/workspace.age
```

Without an output argument, the CLI writes a uniquely named encrypted file under
ignored `backups/`. age prompts for a passphrase. Retain it in your password manager
separately from the backup; there is no passphrase recovery feature. Never provide
the passphrase to the agent or embed it in a Make variable or environment file.

Included: tasks, narratives, current knowledge, repository briefs/bindings,
preferences, custom tools, local artifacts, revision history and credentials.
Excluded: cache, write locks, previous backups, framework source and external repos.
Keep custom files within the documented personal layout. Backups reject links,
nonportable paths and case collisions. Limits: 20,000 files and 1 GiB uncompressed.

The backup records file hashes and verifies the source snapshot remained unchanged.
It stages plaintext in a private temporary directory, encrypts it, publishes the
encrypted output without overwriting an existing file, then removes staging.
Use a local filesystem supporting hard links for atomic publication, then copy the
encrypted result to your backup destination. Abrupt power loss can leave a private
`.bai-backup-*` temporary directory; remove it after confirming no operation is active.

## Restore on this or another machine

Clone a compatible framework version and install Python/age. Restore before starting
the agent or running `init`, because restoration requires `.bai/` to be absent.

```sh
python3 bai.py backup restore --file /path/to/workspace.age
# Or select a separate existing clone:
python3 bai.py --root /path/to/fresh-clone backup restore --file /path/to/workspace.age
```

Windows uses `py -3`. The Make shortcut is `make restore-backup FILE=...`.
An existing `.bai/`, even an empty one, is refused. Restore elsewhere to inspect a
backup without disturbing current work. Do not delete your current data to satisfy
that requirement.

Restore decrypts in private temporary storage, validates schema, member paths,
sizes and hashes, and rejects symlinks, traversal, duplicate paths and corrupt data.
Only a fully validated snapshot is activated. Schema versions newer than v1 are
rejected. Unsupported future versions require a compatible framework, not guessing.
Temporary plaintext is removed after ordinary success/failure; abrupt OS termination
may require cleaning a `.bai-restore-*` directory after confirming no process is active.

After restore:

```sh
python3 bai.py doctor
python3 bai.py repo rebind ticket-api --path /new/location/ticket-api
python3 bai.py memory review
```

Recheck *all* repository bindings when switching machines, even paths that exist:
the same path can contain a different checkout. External repositories and runtime
dependencies are not backed up. The cache is rebuilt; restored tools are never
automatically executed. A structural validation does not make unknown scripts safe.
