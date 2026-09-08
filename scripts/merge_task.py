#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主干的唯一入口。

为什么要有这个脚本：
    防覆盖靠两件事 —— ① 各干各的（worktree 隔离，pre-commit 管）
    ② 一次只落一个（单一入口，pre-push 管 + 本脚本执行）。
    合并位 Agent 说 PASS 不算数，**测试由本脚本亲自跑**，跑出来的结果才作数。

为什么必须在主工作目录合：
    SuperRAN 的 editable 安装把 import 路径硬指向主仓库，
    在 worktree 里跑测试拿到的是主仓库的代码，证据是假的。
    所以「合进去 → 跑测试 → 不过就回滚」这一套必须在主目录做。
    AI HUB 没这个问题，但统一走同一条路，少一种情况要记。

用法：
    python scripts/merge_task.py <分支名> --expected-head <SHA> --expected-trunk <SHA>
    # 加 --dry-run 只验不合；本地入口不执行 fetch/push 或推进发布分支。

项目差异全部读 .agents/project.json，本脚本对项目一无所知。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / ".agents" / "project.json"
_MERGE_LOCK_HANDLE = None


def say(msg=""):
    print(msg, flush=True)


def run(cmd, cwd=None, env=None, check=True, capture=True):
    """跑一条命令。capture=False 时把输出直接透给终端（跑测试用）。"""
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(
        cmd, cwd=str(cwd or REPO), env=e, shell=isinstance(cmd, str),
        capture_output=capture, text=True, encoding="utf-8", errors="replace",
    )
    if check and r.returncode != 0:
        out = (r.stdout or "") + (r.stderr or "") if capture else "(输出见上)"
        raise RuntimeError(f"命令失败（退出码 {r.returncode}）：{cmd}\n{out}")
    return r


def git(*args, **kw):
    return run(["git", *args], **kw).stdout.strip()


def acquire_merge_lock():
    """同一仓库一次只允许一个合并进程触碰主工作区。进程退出时 OS 自动释放锁。"""
    global _MERGE_LOCK_HANDLE
    common = Path(git("rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (REPO / common).resolve()
    lock_path = common / "hub-merge-task.lock"
    handle = open(lock_path, "a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _MERGE_LOCK_HANDLE = handle
    return True


def load_config():
    if not CONFIG.exists():
        say(f"✗ 找不到项目配置：{CONFIG}")
        say("  这个项目还没整理过。先让一个普通 agent 跑 project-prep skill。")
        sys.exit(2)
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        say(f"✗ 项目配置不是合法 JSON：{ex}")
        sys.exit(2)


def main_worktree():
    line = git("worktree", "list", "--porcelain").splitlines()[0]
    return Path(line.replace("worktree ", "", 1).strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("branch", help="要合并的任务分支")
    ap.add_argument("--dry-run", action="store_true", help="试合并和跑测试，不提交")
    ap.add_argument("--expected-head", required=True, help="亲自审核的完整任务 SHA")
    ap.add_argument("--expected-trunk", required=True, help="亲自审核的完整主干 SHA")
    args = ap.parse_args()

    cfg = load_config()
    trunk = cfg.get("trunk") or "master"
    name = cfg.get("name") or REPO.name
    tests = cfg.get("test") or []
    after = cfg.get("afterMerge") or []
    branch = args.branch

    say(f"── {name} · 合并 {branch} → {trunk} ──")
    say()

    if not acquire_merge_lock():
        say("✗ 另一个合并任务正在运行，本次没有触碰主工作区。")
        say("  等前一个结束后再重试；不要并行启动两个合并位。")
        return 2

    # ① 必须在主工作目录 —— 否则 SuperRAN 的测试证据是假的
    here, main_wt = REPO.resolve(), main_worktree().resolve()
    if str(here).lower() != str(main_wt).lower():
        say("✗ 必须在主工作目录跑这个脚本。")
        say(f"  当前：{here}")
        say(f"  主目录：{main_wt}")
        say("  原因：worktree 里的测试可能解析到主仓库的代码，结果不可信。")
        sys.exit(2)

    if not isinstance(tests, list) or not tests or any(
        not isinstance(t, str) or not t.strip() for t in tests
    ):
        say("✗ 项目必须配置非空测试命令，不能无测试合并。")
        return 2
    if git("branch", "--show-current") != trunk:
        say(f"✗ 主工作目录必须已在 {trunk}，本脚本不切换别人的工作分支。")
        return 2
    original = git("rev-parse", f"refs/heads/{trunk}")
    candidate = git("rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    if original != args.expected_trunk or candidate != args.expected_head:
        say("✗ 任务或主干 SHA 已变化，必须重新审核当前版本。")
        return 2
    # 保守拒绝整个脏工作区；既不覆盖，也不把别人已暂存的文件带入合并提交。
    if git("status", "--porcelain"):
        say("✗ 主工作目录有未提交内容，保留原状；请在归属明确后重试。")
        return 2

    say(f"① 记下回滚点：{trunk} = {original[:12]}")

    bypass = {"HUB_ALLOW_MAIN_COMMIT": "1"}
    merged_sha = None
    merged_tree = None

    def rollback():
        """只撤销本次未提交合并；异常状态保留现场，绝不 reset。"""
        merge_state = run(["git", "rev-parse", "--verify", "MERGE_HEAD"], check=False)
        if merge_state.returncode == 0:
            # merge --abort 也会删除测试期间被别人新暂存的文件。
            # 只有现场仍等于本次已知的试合结果，才允许自动撤销。
            if (merged_tree is None
                    or git("rev-parse", "HEAD") != original
                    or merge_state.stdout.strip() != candidate
                    or git("write-tree") != merged_tree
                    or git("diff", "--name-only")
                    or git("ls-files", "--others", "--exclude-standard")):
                raise RuntimeError("合并现场存在未知变化或冲突；未执行 merge --abort，已保留索引、文件和合并状态。")
            run(["git", "merge", "--abort"])
        if git("rev-parse", "HEAD") != original or git("status", "--porcelain"):
            raise RuntimeError("回滚后工作区或 HEAD 与原状态不同；已保留现场，请人工核对。")

    try:
        git("checkout", trunk, env=bypass)

        # 远端同步是维护者另行授权的动作；本次验证只绑定已记录的本地主干。

        # ② 先合进工作区但**不提交** —— 崩溃安全的关键。
        #
        #    老写法是先 `git merge` 生成提交、再跑测试、失败才回滚。
        #    问题是这中间有个几十秒的窗口：脚本一旦被杀（Ctrl+C、SIGPIPE、
        #    终端关掉、机器 OOM），主干就停在「已合但没验」的状态，
        #    而且没人知道。实测踩到过 —— 把输出管道给 head 就触发了 SIGPIPE。
        #
        #    改成 --no-commit 之后：测试期间主干的提交历史根本没动过，
        #    工作区处于 git 自己认得的 MERGING 状态，被杀了也一眼看得出、
        #    只有现场未被额外修改时才允许自动 merge --abort；否则保留现场。
        # 已经合过的分支：--no-commit 下 git 只会说 "Already up to date"，
        # 不产生待提交内容，后面的 commit 就会报个莫名其妙的错。
        # 用户重跑一次合并是很正常的事，得给句人话。
        if run(["git", "merge-base", "--is-ancestor", candidate, original], check=False).returncode == 0:
            say(f"② {branch} 已经在 {trunk} 里了，无需重复合并")
            say()
            say(f"✓ 已是最新：{trunk} @ {original[:12]}")
            return 0

        say(f"② 合并 {branch}（先不提交，测过了再落）")
        git("merge", "--no-ff", "--no-commit", candidate, env=bypass)
        merged_tree = git("write-tree")
        say("   已合进工作区，主干提交历史暂未改变")

        # ⑤ 亲自跑测试 —— 不采信任何 Agent 的说法
        say(f"③ 跑测试（{len(tests)} 条）")
        for i, t in enumerate(tests, 1):
            say(f"   [{i}/{len(tests)}] {t}")
            test_env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            if (REPO / "src").is_dir():
                test_env["PYTHONPATH"] = str(REPO / "src")
            r = run(t, env=test_env, check=False, capture=False)
            if r.returncode != 0:
                raise RuntimeError(f"测试没过：{t}")
        say("   全部通过")

        if (git("rev-parse", "HEAD") != original
                or git("rev-parse", f"refs/heads/{branch}") != candidate
                or git("rev-parse", "MERGE_HEAD") != candidate
                or git("write-tree") != merged_tree
                or git("diff", "--name-only")
                or git("ls-files", "--others", "--exclude-standard")):
            raise RuntimeError("测试期间 SHA、索引或工作区发生额外变化，拒绝提交。")

        if args.dry_run:
            say()
            say("④ --dry-run，撤销不真合")
            rollback()
            say(f"   已回到 {original[:12]}")
            say()
            say("结论：验证通过，可以合。")
            return 0

        # ⑥ 测试过了才真正落成提交
        git("commit", "--no-edit", env=bypass)
        merged_sha = git("rev-parse", "HEAD")
        say(f"   已提交 {merged_sha[:12]}")

    except (Exception, KeyboardInterrupt) as ex:
        say()
        say(f"✗ {ex}")
        say()
        say(f"回滚 {trunk} → {original[:12]}")
        try:
            rollback()
        except Exception as rollback_error:
            say(f"✗ 无法确认完整回滚：{rollback_error}")
            return 2
        say("已回滚，主干没有被污染。")
        say()
        say("这个分支还在，改完再跑一次本脚本即可。")
        return 1

    say("④ 本地合并完成；远端及发布分支保持独立，由维护者另行发起同步。")

    # ⑦ 合并后动作 —— 项目专属的东西全在这里，脚本本身不知道是什么
    if after:
        say(f"⑤ 合并后动作（{len(after)} 条）")
        for a in after:
            cmd = a.replace("{branch}", branch).replace("{sha}", merged_sha or "")
            say(f"   {cmd}")
            r = run(cmd, check=False, capture=False)
            if r.returncode != 0:
                say("   ⚠ 这条失败了，但代码已经合进去了，不回滚")

    say()
    say(f"✓ 已合并：{branch} → {trunk} @ {merged_sha[:12]}")
    say(f"  撤回：git revert -m 1 {merged_sha[:12]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n中断")
        sys.exit(130)
