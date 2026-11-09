# Contributing

Keep framework behavior generic and examples synthetic. Do not include personal
workspace data, company-specific policies, service URLs, identities or credentials.
The runtime uses Python 3.11+ standard library; age is the external backup backend.

Run `python3 -B -m unittest discover -s tests -v` (Windows: `py -3`). Tests use
temporary workspaces, dummy secrets and local subprocesses. They never contact
external services. If age and age-keygen are available, the encrypted recovery
test uses temporary generated test keys without printing them. That test validates
the real age format; passphrase prompts require a separate interactive smoke test.

Changes to record formats must maintain schema compatibility or provide an explicit
migration. Never silently reinterpret older records. Keep agent adapters thin and
the shared instructions concise. Validate manifest examples and documentation links.

Review changes from both perspectives:

- Human: Can a new user start with one request, enter missing keys privately, find
  unfinished work and restore a backup without learning the internal layout?
- Agent: Can a fresh session find related repos, current evidence and reusable tools
  without reading private data or mistaking old approvals for new authorization?

The CI matrix exercises macOS, Linux and Windows. Manual acceptance should also
verify hidden input and age passphrase prompts in real terminals, then start a
fresh session in each claimed agent integration and check that it reads AGENTS.md.
Record unavailable platforms/agents as unverified rather than claiming a live pass.
