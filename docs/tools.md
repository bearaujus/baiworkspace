# Reuse before scripting

Run `tools find "purpose or keywords"`, then `tools show <id>`. Context retrieval
includes matching tools automatically. Check the contract and current source before
execution, particularly for external writes. Record the reuse decision in the task.

Extend an existing tool when its behavior remains compatible. Create a new tool
when requirements differ materially; record why. Extract common helpers when actual
callers demonstrate reuse. A registry cannot prevent an unrestricted agent from
writing another script, but makes its reuse decision visible and testable.

## Manifest contract

Copy `templates/tool.json` to `.bai/artifacts/my-tool.json`, fill every field, and
put your script under `.bai/tools/my-tool/`. Then register it:

```sh
python3 bai.py tools register --file .bai/artifacts/my-tool.json
python3 bai.py tools show my-tool
python3 bai.py tools run my-tool --profile dev -- --input /path/to/input.csv
```

The manifest declares version, ID, description, keywords, relative entry point,
runtime (`python` or `executable`), fixed arguments, executable dependencies,
inputs/outputs, effects, required credential key descriptions, verification and
recovery. Values never belong in the `secrets` mapping. A `repo` ID resolves the
entry relative to a registered external repo; otherwise it is clone-relative.
Built-in examples are framework-relative and cannot be shadowed by local tools.

Classify effects conservatively: `read-only`, `local-write` or `external-write`.
External-write tools require `--execute`, supplied only when the current task
authorizes the operation. This is an intent marker, not a security boundary.
If a script has separate planning and execution modes, register separate IDs with
different fixed arguments/effects so their contracts are unambiguous.

Use `tools register --replace` for a reviewed local manifest update. Set
`replaced_by` on retired manifests. The runner refuses a retired tool and points to
the replacement. Keep the old source when it is still historical evidence.

## Execution and recovery

The runner launches argv directly with `shell=False`. Python entry points use the
same interpreter as the CLI. Native executable entry points must be runnable on
the current OS; `.bat`/`.cmd` entry points are rejected. A portable framework does
not make platform-specific user scripts portable.

Only essential process environment variables and the declared keys from the chosen
profile are passed. Existing CLI login/config files may still be accessible through
the user's home directory. This runner is not a sandbox. Ambient credential and
proxy environment variables are not automatically inherited.

Tool stdin is closed. Keep interactive setup separate and user-operated. Stdout and
stderr are captured privately, known injected secrets and their URL-encoded forms
are redacted, and output is limited to 1 MiB per stream. Temporary capture files
are removed on close; abrupt OS termination may leave OS-managed temporary data.
Arbitrary encodings and secrets discovered by the child itself cannot be universally
redacted. Tools must never log credentials or persist them in their own artifacts.

Child exit codes propagate; `--json` includes the code and recovery text. Timeout
defaults to 300 seconds and is configurable. The direct child is killed on timeout
or interruption, but detached descendants and submitted remote work can continue.
Use reconciliation rather than blind retries. No automatic retry or rollback occurs.

## Included tools

- `csv-summary`: validates header/row width and reports columns and counts without
  cell values. Preserves duplicate row counts and accepts UTF-8 BOM input.
- `demo-service-check`: synthetic credential setup demonstration, with no network.

```sh
python3 bai.py tools run csv-summary -- /path/to/input.csv
```
