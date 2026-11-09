# Private credential entry

The agent prepares a tool and its manifest first, then asks you to run one setup
command yourself. Do not paste values into chat or command-line arguments.

```sh
python3 bai.py secrets setup --tool demo-service-check --profile dev
python3 bai.py secrets list --profile dev
python3 bai.py secrets set DEMO_SERVICE_TOKEN --profile dev
python3 bai.py secrets remove DEMO_SERVICE_TOKEN --profile dev
```

Windows: replace `python3` with `py -3`. Make equivalents are `secrets-setup`,
`secrets-list`, `secret-set` and `secret-remove`, with `TOOL`, `PROFILE` and `KEY`.

`setup` prompts only for missing keys declared by that tool. `set` adds or updates a
key with hidden input. Keys contain letters, digits and underscores, and cannot
start with a digit. Profile names contain lowercase letters, digits and hyphens.
Values are nonempty single-line strings. No secret argument, pipe input, plaintext
export or secret-revealing command exists. Setup fails if hidden input is unavailable.

`list` returns descriptions, configured/missing status and consuming tools. Removal
names affected tools and asks for terminal confirmation; explicit `--yes` supports
an already authorized noninteractive removal. Interrupted setup leaves the store
unchanged. Concurrent credential updates fail rather than overwrite one another.

The default profile is `dev`. Choose a different profile explicitly for other
environments. Tools receive only declared keys from that profile; there is no
fallback to another profile. Check account identity using a service's identity
endpoint before consequential operations when supported.

## Storage boundary

Values live in Git-ignored `.bai/private/secrets.json`. Atomic writes use private
temporary files (mode 0600 on POSIX) and private directories (0700). Windows uses
the current user's inherited filesystem ACLs; choose a private user-owned clone
directory. The store is plaintext, not an OS keychain or protection against another
process with the same filesystem access. Do not force-add personal files to Git.

The agent uses metadata commands and the runner. It must not read the store into
its context. Known injected values are redacted from captured output, but tool
authors remain responsible for artifacts and other secret encodings. See the
[runner boundary](tools.md). Encrypted backups include the store.

For existing `.env` or cloud profiles, keep using the tool's documented runtime
credential path or manually enter the needed values through `secrets set`. The
workspace never automatically scans, imports or prints existing credential files.
