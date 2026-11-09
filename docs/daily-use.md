# Daily use

Start an agent in the clone and describe what you need. The agent reads the shared
instructions and initializes `.bai/` when writes are allowed. It does not need your
entire repo catalogue before helping with the first task.

| You say | Expected agent behavior |
| --- | --- |
| Hello | Brief readiness and current task status; no automatic execution |
| I work in this repo | Register its path, inspect local instructions and create a brief |
| Here is how the service works | Record a sourced explanation and update connected system maps |
| Investigate this failure | Retrieve relevant evidence and tools, then verify current state |
| Use only these rows | Replace the current scope and preserve the correction before proceeding |
| Continue yesterday's work | Find the correct handoff; check current files and authority |
| How far did you get? | Explain the current result and next step; continue authorized work |
| Stop for now | Preserve the current handoff and stop execution |
| Review what you remember | Reconcile stale, overlapping and superseded memories |
| Back up my workspace | Provide the terminal command; the user enters the encryption passphrase |

## Human responsibilities

Explain intent and correct assumptions. Supply repository locations when they
cannot be discovered from the registry. Enter credentials in your own terminal.
Review concrete consequential changes. Keep backups somewhere recoverable if
this machine fails, with the passphrase stored separately.

## Agent responsibilities

Retrieve before exploring; reuse before scripting; verify before claiming success.
Keep explanations concise and continue work already authorized. A saved credential
or past approval does not authorize a new external operation. Respect the host
agent's actual filesystem and network permissions, including access to sibling repos.

Do not turn every small question into a task. Record meaningful work with a dated
ID and maintain it across follow-ups. Multiple tasks may be active; there is no
single global task slot. Use a non-secret patch file under `.bai/artifacts/` to
update record fields. CLI revision checks protect against concurrent stale updates.

```sh
python3 bai.py task new 2026-11-10-service-failure --title "Investigate service failure"
python3 bai.py task show 2026-11-10-service-failure --json
python3 bai.py task update 2026-11-10-service-failure --file .bai/artifacts/handoff.json --expected-revision 1
python3 bai.py checkpoint 2026-11-10-service-failure --expected-revision 2
```

A handoff patch might be:

```json
{
  "scope": "Inspect the failed import and prepare a local fix; no deployment.",
  "summary": "Input parser fix prepared and verified against synthetic records.",
  "status": "complete",
  "verification": "Targeted parser regression tests passed; production unverified.",
  "deployment": "Not deployed.",
  "reuse_decision": "Used existing csv-summary to confirm the input shape.",
  "tools": ["csv-summary"],
  "next_step": "User reviews the patch before deployment."
}
```

Edit the matching Markdown narrative with the evidence and decision rationale.
Read the narrative and handoff before checkpointing. Completion means the requested
outcome was reached; it does not imply deployment. Store input snapshots and
verification artifacts in `.bai/artifacts/`, with explicit source references.

## Interrupted work and partial outcomes

Keep successful, failed, unsubmitted and uncertain operations distinct. A timeout
can happen after a server accepted a request. Tools should supply a read-back or
reconciliation procedure; agents should not retry external writes blindly. The
runner cannot roll back remote work or prove that remote cancellation succeeded.

`doctor` and `memory review` are read-only. Do not run repair, initialization or
checkpoint commands in plan/read-only sessions. Report what remains to capture.

## Backups and upgrades

The framework repo can be updated without committing `.bai/`. Back up first when
changing versions. v1 readers reject unsupported schema versions; future data
migrations must be explicit. There is no scheduler, auto-updater, background agent
or automatic Git operation. Use [backup and restore](backup.md) for recovery.
