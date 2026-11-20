# Connected, durable memory

The agent reads a small startup contract, then retrieves only relevant knowledge.
Memory is ordinary Markdown plus versioned JSON metadata. Search excludes private
credentials, raw artifacts and historical snapshots. There is no embedding service.

## Personal layout

| Location under `.bai/` | Purpose |
| --- | --- |
| `workspace.json` | Portable repository IDs, aliases and summaries, plus the optional topic registry |
| `bindings.json` | Machine-specific repository paths |
| `preferences.md` | Durable user working preferences |
| `repos/` | Short repository briefs |
| `knowledge/` | Current learning, system, procedure and preference records/narratives |
| `tasks/` | Current handoffs and task narratives |
| `tools/` | Personal tool manifests and custom script directories |
| `private/` | Credential store; never read into agent context |
| `artifacts/` | Task inputs, evidence and outputs; excluded from search |
| `history/` | Prior record revisions; excluded from default retrieval |
| `cache/` | Disposable derived index |

## Register repositories

```sh
python3 bai.py repo add ticket-api --path /path/to/ticket-api --summary "Receives ticket requests" --aliases tickets frontend
python3 bai.py repo add policy-config --path /path/to/policy-config --summary "Configures approval rules"
python3 bai.py repo add cloud-ops --path /path/to/cloud-ops --summary "Runs direct maintenance jobs"
```

The paths must exist. This does not clone, change or authorize work in those repos.
Edit each generated brief with source entry points, test commands and relationships.

## Teach a connected flow

Create a task narrative recording the explanation and its date. Then create a
system memory and edit its generated Markdown with the full flow and exceptions:

```sh
python3 bai.py memory add request-flow --kind system --title "Ticket request flow" --repos ticket-api policy-config cloud-ops
```

Create a JSON update file under `.bai/artifacts/` with these fields:

```json
{
  "summary": "Ticket API evaluates configured policies. Cloud operations has a separate direct maintenance path.",
  "aliases": ["tickets", "request flow", "approval"],
  "status": "current",
  "provenance": "user-stated",
  "sources": ["workspace:tasks/2026-11-10-flow-explanation.md"],
  "relationships": [
    {"from": "policy-config", "to": "ticket-api", "relation": "configures approval policy"},
    {"from": "cloud-ops", "to": "ticket-api", "relation": "operates shared infrastructure through an independent maintenance path"}
  ]
}
```

The cited narrative must exist before attestation. Sources are explicit references:
`workspace:tasks/name.md` or `ticket-api:src/router.py`. External source references
are resolved through the repository binding, never a hardcoded machine path.
Credential-shaped filenames and Git internals are disallowed as evidence sources.

```sh
python3 bai.py memory update request-flow --file .bai/artifacts/flow.json --expected-revision 1
python3 bai.py memory review
python3 bai.py memory attest request-flow --expected-revision 2
python3 bai.py context --repo ticket-api --query "request flow" --json
```

This retrieves the system map and connected briefs even when only `ticket-api` is
named. It does not claim that direct maintenance executes as part of a user request.

## What to record

All task/memory records use schema version 1, ID, title, summary, revision, update
time, repo IDs and aliases. Narratives share the record basename. Use `show --json`
to inspect the complete shape; `update --file` merges supported fields and rejects
identity, revision and review-hash edits. Unknown update fields are rejected.

Tasks have status (`active`, `waiting`, `complete`, `cancelled`, `superseded`,
`unknown`), scope, verification, deployment, next step, reuse decision, tools,
learnings and evidence descriptions. `learnings` contains related memory IDs.
Optional task fields include `topic`, `done_when` and `waiting_on`. Use plain text
for `done_when`. When a task is waiting, record `user` for a needed user decision
or action, `external` for a dependency outside the user's control, or `unknown`
when responsibility is unclear. Earlier records may omit these fields. Task aliases
help a new chat find the same handoff using the user's terminology.

Memories have kind (`learning`, `system`, `procedure`, `preference`), status
(`current`, `uncertain`, `superseded`), provenance, sources, related `links`,
`supersedes` IDs and directed relationships. Relationship endpoints must be listed
in the memory's repo IDs. Provenance is `user-stated`, `source-verified`,
`runtime-observed`, `inference` or `historical-record`.

Use `uncertain` for unresolved claims. Describe verification limits in the narrative.
A structural check cannot establish truth, current deployment or authorization.

## Group recurring work into topics

A topic gives related tasks a stable name, purpose, repository roles and requirement
references. Store durable requirements in memory, where their sources, provenance
and review state can be maintained. Use repository IDs for the reference and
implementation roles. The roles describe where to read and where changes usually
belong. Verify the current scope and authorization before acting.

```sh
python3 bai.py topic add ticketing --title "Ticketing" --purpose "Maintain ticket request flow" --aliases "ticket service" --reference-repos policy-config --implementation-repos ticket-api --requirements request-flow
python3 bai.py topic show "ticket service" --json
python3 bai.py topic show ticketing --history --json
```

`topic show` includes current open tasks and an `open_set` token. `--history` adds
closed and historical tasks. Topics and task membership are optional in existing
workspaces. To group an existing task, patch its `topic` with the topic ID or alias
using `task update`. Use `null` to clear membership. To revise topic metadata, use
`topic update ticketing --file <patch.json> --expected-revision <revision>` with
the revision from `topic show`.

Check open work first and reuse a matching task. When the request needs a new task,
pass the observed token from `topic show`, `task list --open --topic <id>` or a
brief for the same topic.

```sh
python3 bai.py task new 2026-11-20-ticket-timeout --title "Investigate request timeouts" --topic ticketing --done-when "Reproduce the timeout and verify the service behavior" --expected-open-set "TOKEN_FROM_TOPIC_SHOW"
```

The token covers the current open task IDs and revisions for that topic, including
an empty set. If it changes, the CLI reports the number of open candidates and up
to five matches. Review those tasks before deciding whether to create a task.
Ungrouped tasks can use the global token from `task list --open` or an unscoped
brief when they need the same check.

## Correct and review

When the user corrects a flow, update its current summary and narrative. Keep the
old explanation in the narrative's history section; metadata revisions are also
preserved automatically. For a replacement memory, link the old ID in `supersedes`
and mark the old memory superseded. Default retrieval demotes superseded material;
queries about history can still find it.

Review affected memories after new repos, new explanations, observed behavior
changes and scope corrections. `memory review --repo <id>` reports changed source
hashes, changed narratives, missing links, incomplete maps and possible alias
overlap. The agent reads the evidence and decides what changes are warranted.
Attestation records that review; it never upgrades provenance or certainty itself.
Context flags a retrieved current memory with `review_needed` when its evidence
changed, is unavailable or has yet to be reviewed. Inspect the cited evidence
before relying on that memory.

`checkpoint` validates a task's declared scope and reuse choice, stamps its narrative
and metadata hashes, and returns the committed revision. It also tries to refresh
the derived index and run a memory review. A failure in either follow-up appears as
a warning on the successful checkpoint, so use the returned revision for later edits.
Context builds from current records even when the cache is missing or stale. Invalid
individual records are reported alongside valid work.

The workspace uses a short-lived write lock and expected record revisions. If a
process crashes, verify it has stopped before manually removing `.bai/write.lock`.
Do not remove a live writer's lock. Direct Markdown edits remain possible; review
hashes reveal changes since the last checkpoint.

## Historical workspace imports

Imported records may set `historical: true` and an `origin` object containing the
original source/date and import time. Preserve unknown/waiting outcomes and old
verification limits. Do not stamp a historical import as newly verified merely to
clear health warnings. `status` focuses on current tasks; `--include-historical`
includes old unfinished records. `memory review --include-historical` expands the
on-use historical review queue. Normal doctor output reports that queue as counts.

Keep ongoing umbrella tasks separate from their historical completed subtasks.
Context reserves early slots for current handoffs, system maps and reusable tools;
historical tasks come afterward. Review an imported reference when it becomes
relevant and update its current summary/provenance only after actual evidence review.

Explicit `evidence:relative/path` sources address preserved files under
`.bai/artifacts/`. They are hashed during explicit review but never added to search
content. This supports self-contained migration while keeping raw evidence out of
normal agent context. Credential file names, private directories and traversal are
still rejected. Preserving evidence is not permission to execute old commands.
