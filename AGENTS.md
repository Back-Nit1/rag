# AGENTS.md

Read [`CLAUDE.md`](./CLAUDE.md) first. It is the single set of agent
instructions for this repository — layout, commands, git cadence and the
non-negotiable rules — and it applies to every coding agent, not only Claude
Code.

Role-scoped spec extracts live in `docs/spec/`. Read only the block your task
needs; the table in `CLAUDE.md` maps task to block.

Current state, path ownership and the frozen base SHA for new work:
[`docs/progress.md`](./docs/progress.md).

Two rules worth repeating here because they are the ones most often broken by
an agent that skipped `CLAUDE.md`:

- `packages/contracts/` is coordinator-owned. Propose schema changes; never
  fork a second schema.
- Stage explicit paths. `git add -A` and `git add .` are forbidden.
