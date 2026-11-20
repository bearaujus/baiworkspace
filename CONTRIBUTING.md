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

## Git delivery

Use this workflow for baiworkspace and as the default for any repository you
work on through Bai. Follow the target repository's Git conventions and release
workflow wherever they define their own rules.

### Scope and naming

- Give each independently reviewable feature, fix or maintenance change a branch
  and PR of its own. Start from the current intended base, normally the remote
  default branch. Keep successive commits for the same change on that branch.
- Name new agent branches `codex/<type>-<short-kebab-case-summary>`, for example
  `codex/fix-context-order`. Reuse an existing branch for the same change.
- Use Conventional Commits for commit subjects and PR titles. Write
  `type(scope): short imperative summary`, with the scope optional. Common types
  include `feat`, `fix`, `docs`, `refactor`, `test`, `ci` and `chore`. For example,
  `fix(context): preserve result order` names a bug fix clearly. A PR title
  describes the outcome of the whole branch and can serve as its squash-merge
  subject if the repository uses squash merges.
- Make each commit a logical change. Stage specific paths or use `git add -p` when
  a file contains unrelated edits. Review `git diff --cached` before committing.
  Keep tests with the change they verify when they belong to the same unit.

### Before publishing

1. Check the worktree, current branch and destination remote. Fetch current refs
   and confirm the intended PR base. In the example below the base is `origin/main`
   and `HEAD` is the feature branch being reviewed.

   ```sh
   git log --oneline origin/main..HEAD
   git diff --stat origin/main...HEAD
   git diff origin/main...HEAD
   ```

2. Confirm every listed commit and changed hunk belongs to the PR. If several
   independent changes already share a branch, preserve that branch and prepare
   clean branches from the intended base. Bring over the relevant commits or
   hunks for each change. When a separate PR depends on an unmerged PR, name the
   dependency and use its branch as the base. After it lands, confirm the base and
   diff again before continuing review.
3. When a push is authorized, use an explicit remote and destination for the
   reviewed branch. For example, the first push of `codex/fix-context-order` is
   `git push -u origin HEAD:refs/heads/codex/fix-context-order`. Git publishes the
   commits needed for that branch tip in a single push, so keep independent work
   on separate branches from the start. Push directly to the default branch only
   when the current authorization and repository workflow allow it.
4. When opening a PR is authorized, confirm the base and head and use the title
   format above. Describe what changed, why, and which checks you ran. Use the
   repository's PR template if it has one. For later changes to the same PR,
   review the commits since the remote head and push to the same branch.

References for the workflow and commit format are [GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow),
[GitHub's PR review guidance](https://docs.github.com/en/pull-requests/concepts/helping-others-review-your-changes),
[Pro Git's commit guidance](https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project),
and [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).

## Review

Review changes from both perspectives:

- Human: Can a new user start with one request, enter missing keys privately, find
  unfinished work and restore a backup without learning the internal layout?
- Agent: Can a fresh session find related repos, current evidence and reusable tools
  without reading private data or mistaking old approvals for new authorization?

The CI matrix exercises macOS, Linux and Windows. Manual acceptance should also
verify hidden input and age passphrase prompts in real terminals, then start a
fresh session in each claimed agent integration and check that it reads AGENTS.md.
Record unavailable platforms/agents as unverified rather than claiming a live pass.
