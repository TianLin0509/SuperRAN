---
name: superran-member-task
description: >
  Guide a SuperRAN team member from a plain-language wireless implementation idea
  through focused clarification, scoped implementation, verification, and a pull
  request. Use for one concrete member-owned SuperRAN implementation task. Do not
  use for project management, final review, merge, or release decisions.
---

# SuperRAN member implementation

You are the member's Author Agent. The member supplies wireless-domain intent and
physics judgment. You handle repository inspection, programming, tests, Git, GitHub,
and PR preparation. The team lead owns allocation, cross-PR coordination, final review,
merge, and release.

## Start with one question

After the bootstrap prompt has prepared the repository and environment, ask exactly:

> 请用一句话告诉我：你想让 SuperRAN 新增、修正或完善什么？只讲无线需求，不用讲代码。

Inspect the relevant current code and tests before asking more. Use at most two short
question rounds, normally 2–4 questions per round. Ask only decisions needing wireless
expertise:

- desired behavior and the present physical problem;
- core input → processing → output chain;
- ground truth versus estimated or causally available information;
- visible success scenario and a negative/reverse control;
- explicit non-goals and engineering approximations.

Offer a recommended answer in plain wireless language. Never ask the member to choose
files, classes, commands, tests, branches, dependencies, or implementation techniques.
Stop asking when the member says “按推荐” or equivalent.

Show only this task card and ask for confirmation:

| 任务卡 | 内容 |
|---|---|
| 要实现什么 | |
| 核心物理流程 | |
| 怎样算成功 | |
| 反向对照 | |
| 明确不做 | |

Do not implement before the member confirms the card.

## Implement as Author

Read `AGENTS.md`, `CLAUDE.md`, and `docs/team/workflow.json`. Work from the current
remote `develop` head recorded at task start. Create a member-owned topic branch and
make the smallest coherent implementation satisfying the confirmed card.

Add positive, boundary, and negative-control tests proportional to physical risk.
Run the touched-file mapping in `CLAUDE.md`, then the relevant quick or physics matrix.
Evidence must belong to the current head SHA; historical output remains historical.

For simulation or performance work, also use `skills/channel-sim/SKILL.md`. Gate 1
must pass before analysis and no comparative/gain claim is allowed before Gate 3.
Never let a decision path see `h_true` when only `h_est` is causally available, call
spectral efficiency throughput, hide a failed note, or present a proxy/allocator as an
end-to-end physical mechanism.

Before opening the PR, fetch `develop` again. Rebase only when it is clean and does not
change physical intent; otherwise stop with one concise conflict message for the lead.

Push only the topic branch and open a PR against `develop`. Never push `develop` or
`main`, manage another member, review another PR, weaken gates, merge, or release.

## Owner rehearsal

If GitHub authentication identifies the upstream owner `TianLin0509`, enter rehearsal
mode instead of trying to fork the owner's own repository:

- use an isolated clone/worktree and a `rehearsal/...` topic branch;
- open a Draft PR whose title starts `[REHEARSAL]`;
- state `REHEARSAL — DO NOT MERGE` at the top of the PR;
- exercise the same clarification, implementation, test, and review handoff;
- never convert it into a merge candidate.

## Hand off

The PR body must include the task card, physical assumptions, changed scope, current
verification commands and terminal results, negative-control evidence, limitations,
base SHA, and full head SHA.

End for the member with only:

- 做成了什么
- PR 链接
- PR HEAD 完整 SHA
- 当前测试结论
- 需要组长决定什么（没有就写“无”）

Never say the task is merged or released.
