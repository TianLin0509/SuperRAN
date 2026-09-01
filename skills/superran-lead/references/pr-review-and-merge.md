# PR review and merge

## Review in isolation

Read the PR metadata, task card, base branch, full head SHA, diff, checks, and comments.
Fetch the PR into a clean isolated worktree; never reuse the Author worktree or modify
the Author branch.

Hard-stop when the PR does not target `develop`, the advertised SHA differs from the
remote head, the worktree is dirty, required evidence is historical, or the PR title
starts `[REHEARSAL]` and the requested action is merge.

Run two independent passes against the same head SHA:

1. **Physics pass** — causal information boundary, truth versus estimate, axes, units,
   clocks, power/resource conservation, standard versus engineering assumption,
   mechanism closure, positive scenario, negative/reverse control, Gate 1/2/3 claim
   boundary. For non-physics changes, prove why behavior is unchanged.
2. **Integration pass** — task scope, upstream/downstream contracts, error propagation,
   silent fallback, current-SHA tests, documentation/install drift, packaging, and
   unrelated changes.

Run the touched-file commands from `CLAUDE.md`, then the relevant quick/physics matrix.
If the remote head changes at any point, mark the review stale and stop.

Return only:

| LEAD REVIEW | 内容 |
|---|---|
| PR / reviewed full SHA | |
| 实际改变 | |
| 物理审 | PASS / BLOCK / N/A + strongest evidence |
| 集成审 | PASS / BLOCK + strongest evidence |
| 最强反证 | |
| 允许声称 / 禁止声称 | |
| 必须修改 | none / ordered items |
| 剩余风险 | |
| 结论 | BLOCKED / REVISE / MERGE-CANDIDATE / REHEARSAL-PASS |

Post review comments or a change request only when the lead asks. Send fixes back to the
original member; do not implement them yourself.

## Merge gate

Merge only after the human lead explicitly says, in substance:

`同意合并 PR #<number>，HEAD <full-sha>`

Immediately re-fetch and require all of these:

- PR number and current full head SHA exactly match the approval;
- base is `develop`, PR is not Draft, and title is not `[REHEARSAL]`;
- review verdict for this SHA is `MERGE-CANDIDATE`;
- required/current tests pass and no unresolved BLOCK/REVISE item remains;
- GitHub reports the PR mergeable.

Use squash merge, the team convention in `docs/team/workflow.json`. After merging,
verify GitHub reports the PR merged and remote `develop` contains the result. Report the
merged PR, reviewed head SHA, resulting `develop` SHA, checks, and any post-merge risk.

Publishing `develop` to `main` is a different action and requires separate explicit
authorization plus a current full regression.
