# Connected, durable memory

The agent reads a small startup contract, then retrieves only relevant knowledge.
Memory is ordinary Markdown plus versioned JSON metadata. Search excludes private
credentials, raw artifacts and historical snapshots. There is no embedding service.

## Personal layout

| Location under `.bai/` | Purpose |
| --- | --- |
| `workspace.json` | Portable repository IDs, aliases and summaries |
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

Memories have kind (`learning`, `system`, `procedure`, `preference`), status
(`current`, `uncertain`, `superseded`), provenance, sources, related `links`,
`supersedes` IDs and directed relationships. Relationship endpoints must be listed
in the memory's repo IDs. Provenance is `user-stated`, `source-verified`,
`runtime-observed` or `inference`.

Use `uncertain` for unresolved claims. Describe verification limits in the narrative.
A structural check cannot establish truth, current deployment or authorization.

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

`checkpoint` validates a task's declared scope/reuse choice, stamps its narrative
and metadata hashes, and refreshes the derived index. It also reports memory review
findings. Context builds from current records even when the cache is missing or
stale. Invalid individual records are reported instead of hiding all valid work.

The workspace uses a short-lived write lock and expected record revisions. If a
process crashes, verify it has stopped before manually removing `.bai/write.lock`.
Do not remove a live writer's lock. Direct Markdown edits remain possible; review
hashes reveal changes since the last checkpoint.
