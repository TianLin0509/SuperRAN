#!/usr/bin/env python
"""Small Git transport for the cloud workflow; simulation/review stays local.

Receipts prevent accidental stale publication. They are not signatures or proof
of independent review: Gitea roles and the MERGER contract enforce that boundary.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args, capture=True, env=None):
    result = subprocess.run(list(args), cwd=ROOT, env={**os.environ, **(env or {})},
                            capture_output=capture, text=True, encoding="utf-8",
                            errors="strict", creationflags=(0x08000000 if os.name == "nt" else 0))
    if result.returncode:
        # Git can include credential-bearing URLs in diagnostics; never echo them.
        raise RuntimeError(f"命令 {args[0]} 失败，退出码 {result.returncode}；检查权限、网络或仓库状态。")
    return (result.stdout or "").strip()


def git(*args):
    return run("git", *args)


def config():
    return json.loads((ROOT / ".agents/project.json").read_text(encoding="utf-8"))


def cloud():
    value = config().get("cloudRepository")
    if not value or not value.get("url"):
        raise RuntimeError("本版本没有云端仓库配置。")
    return value


def sha(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("必须提供完整的 40 位 SHA。")
    return value


def clean():
    if git("status", "--porcelain"):
        raise RuntimeError("工作区有未提交内容，保留原状，请先核对归属。")


def primary():
    main = Path(git("worktree", "list", "--porcelain").splitlines()[0][9:]).resolve()
    if ROOT.resolve() != main:
        raise RuntimeError("此操作必须在本机主仓库执行。")


def remote():
    cfg = cloud()
    name = cfg.get("remote", "origin")
    if git("remote", "get-url", "--all", name).splitlines() != [cfg["url"]]:
        raise RuntimeError("远端读取地址与权威仓库不一致，请运行 init 核对迁移。")
    if git("remote", "get-url", "--push", "--all", name).splitlines() != [cfg["url"]]:
        raise RuntimeError("远端推送地址与权威仓库不一致。")
    return name


def remote_sha(name, branch):
    rows = git("ls-remote", "--heads", name, f"refs/heads/{branch}").splitlines()
    return rows[0].split()[0] if rows else "EMPTY"


def receipt_path(commit):
    common = Path(git("rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = ROOT / common
    return common.resolve() / "superran-cloud-receipts" / f"{sha(commit)}.json"


def validate_receipt(commit, remote_base):
    receipt = json.loads(receipt_path(commit).read_text(encoding="utf-8"))
    parents = git("rev-list", "--parents", "-n", "1", sha(commit)).split()
    if (receipt.get("status") != "MERGED" or receipt.get("merged") != commit
            or parents != [commit, receipt.get("base"), receipt.get("candidate")]
            or receipt.get("repository") != cloud()["url"]):
        raise RuntimeError("合并回执与目标提交、父提交或仓库不一致。")
    if remote_base != "EMPTY":
        # Every unpublished first-parent merge needs its own gate receipt.
        git("merge-base", "--is-ancestor", sha(remote_base), commit)
        if remote_base != receipt["base"]:
            validate_receipt(receipt["base"], remote_base)
    return receipt


def init_repo(args):
    primary()
    cfg = cloud()
    name = cfg.get("remote", "origin")
    names = git("remote").splitlines()
    if name in names and git("remote", "get-url", name) == cfg["url"]:
        # Refuse before any mutation when a custom push destination is present.
        remote()
    if name in names and git("remote", "get-url", name) != cfg["url"]:
        old = git("remote", "get-url", name)
        if "github.com" not in old.lower() or "github-archive" in names:
            raise RuntimeError("现有远端不是可自动保留的 GitHub 来源，未修改。")
        git("remote", "rename", name, "github-archive")
        names = git("remote").splitlines()
    if name not in names:
        git("remote", "add", name, cfg["url"])
    # Keep fetch URLs/history but disable every GitHub push URL locally.
    for item in git("remote").splitlines():
        if "github.com" in git("remote", "get-url", item).lower():
            git("config", "--replace-all", f"remote.{item}.pushurl", "disabled://github-archive")
            for row in git("for-each-ref", "--format=%(refname:short)|%(upstream:remotename)", "refs/heads").splitlines():
                branch, upstream_remote = row.split("|", 1)
                if upstream_remote == item:
                    git("config", f"branch.{branch}.remote", name)
    remote()
    git("config", f"branch.{config()['trunk']}.remote", name)
    git("config", f"branch.{config()['trunk']}.merge", f"refs/heads/{config()['trunk']}")
    git("config", "core.hooksPath", ".githooks")
    git("config", "push.default", "nothing")
    print("已启用阿里云日常远端与本机钩子；GitHub 历史读取保留、推送禁用。")


def doctor(args):
    name = remote()
    if git("config", "--get", "core.hooksPath") != ".githooks":
        raise RuntimeError("仓库钩子未启用，请先运行 init。")
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 需要 3.10 或以上。")
    run(sys.executable, "-c", "import pathlib,superran,numpy,scipy,yaml,filelock,mcp; "
        "p=pathlib.Path(superran.__file__).resolve(); "
        "assert p.is_relative_to(pathlib.Path('src').resolve()),str(p); print(p)",
        env={"PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}, capture=False)
    state = {"local": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
             "clean": not bool(git("status", "--porcelain")),
             "cloud": remote_sha(name, config()["trunk"]) if args.online else "未联网验证"}
    print(json.dumps(state, ensure_ascii=False, indent=2))


def start(args):
    primary()
    name = remote()
    if not re.fullmatch(r"(?:feat|fix|chore)/[A-Za-z0-9._/-]+", args.branch):
        raise RuntimeError("任务分支需要 feat/、fix/ 或 chore/ 前缀。")
    git("check-ref-format", f"refs/heads/{args.branch}")
    trunk = config()["trunk"]
    git("fetch", "--no-tags", name, f"refs/heads/{trunk}")
    base = git("rev-parse", "FETCH_HEAD")
    target = Path(args.path).resolve()
    if target.exists() or target == ROOT or ROOT in target.parents:
        raise RuntimeError("任务路径必须是仓库外的新目录。")
    git("worktree", "add", "-b", args.branch, str(target), base)
    print(json.dumps({"worktree": str(target), "base": base, "branch": args.branch}, ensure_ascii=False))


def submit(args):
    clean()
    name = remote()
    branch = git("branch", "--show-current")
    if not branch.startswith(("feat/", "fix/", "chore/")):
        raise RuntimeError("只能提交任务分支。")
    candidate = git("rev-parse", "HEAD")
    base = sha(args.base)
    git("merge-base", "--is-ancestor", base, candidate)
    git("fetch", "--no-tags", name, f"refs/heads/{config()['trunk']}")
    git("merge-base", "--is-ancestor", base, git("rev-parse", "FETCH_HEAD"))
    git("push", name, f"{candidate}:refs/heads/{branch}")
    if remote_sha(name, branch) != candidate:
        raise RuntimeError("候选上传后读回不一致，未形成有效交付。")
    print(json.dumps({"branch": branch, "candidate": candidate, "base": base,
                      "status": "SUBMITTED", "review": "pending"}, ensure_ascii=False))


def fetch_candidate(args):
    name = remote()
    expected = sha(args.sha)
    git("check-ref-format", f"refs/heads/{args.branch}")
    git("fetch", "--no-tags", name, f"refs/heads/{args.branch}")
    if git("rev-parse", "FETCH_HEAD") != expected:
        raise RuntimeError("候选分支已变化，拒绝沿用旧审核。")
    local = f"review/{expected}"
    existing = git("branch", "--list", local)
    if existing:
        if git("rev-parse", local) != expected:
            raise RuntimeError("本地审核分支已被修改，保留现场。")
    else:
        git("branch", local, expected)
    print(local)


def merge(args):
    primary()
    clean()
    candidate, base = sha(args.sha), sha(args.base)
    gate_tests = config()["test"]
    name = remote()
    observed = remote_sha(name, config()["trunk"])
    if observed != base and not (args.initial and observed == "EMPTY"):
        raise RuntimeError("云端主干与审核基线不一致；重新获取并审核。")
    # Existing script owns locking, full tests, rollback and the final commit.
    run(sys.executable, str(ROOT / "scripts/merge_task.py"), args.branch,
        "--expected-head", candidate, "--expected-trunk", base, capture=False)
    result = git("rev-parse", "HEAD")
    if git("rev-list", "--parents", "-n", "1", result).split() != [result, base, candidate]:
        raise RuntimeError("闸门未产生预期的合并提交，未生成回执。")
    path = receipt_path(result)
    path.parent.mkdir(exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump({"status": "MERGED", "merged": result, "base": base, "candidate": candidate,
                   "repository": cloud()["url"], "tests": gate_tests}, stream, indent=2)
    print(f"已验证本地合并 {result}；尚未发布到云端。")


def publish(args):
    primary()
    clean()
    name = remote()
    trunk = config()["trunk"]
    commit = sha(args.sha)
    if git("branch", "--show-current") != trunk or git("rev-parse", "HEAD") != commit:
        raise RuntimeError("当前主干不是已审核的发布提交。")
    base = args.expected_remote
    if base == "EMPTY" and not args.initial:
        raise RuntimeError("首次迁移需要显式 --initial。")
    if base != "EMPTY":
        sha(base)
    validate_receipt(commit, base)
    observed = remote_sha(name, trunk)
    if observed == commit:
        print(f"云端 {trunk} 已是 {commit}，无需重复发布。")
        return
    if observed != base:
        raise RuntimeError("云端主干已变化，停止发布并重新审核。")
    env = {"SUPERRAN_CLOUD_PUBLISH": commit, "SUPERRAN_CLOUD_BASE": base}
    # Normal push, never force; hook also compares server-advertised old SHA.
    run("git", "push", name, f"{commit}:refs/heads/{trunk}", env=env)
    if remote_sha(name, trunk) != commit:
        raise RuntimeError("云端读回与发布提交不一致，需核对并发更新。")
    print(f"云端 {trunk} 已读回 {commit}")


def check_push(args):
    cfg = cloud()
    if args.url != cfg["url"]:
        raise RuntimeError("日常推送只允许阿里云权威仓库。")
    for line in sys.stdin:
        local_ref, commit, remote_ref, old = line.split()
        protected = {"main", "master", config()["trunk"], *config().get("protectedBranches", [])}
        if remote_ref.removeprefix("refs/heads/") not in protected:
            continue
        base = "EMPTY" if old == "0" * 40 else old
        if (remote_ref != f"refs/heads/{config()['trunk']}"
                or os.environ.get("SUPERRAN_CLOUD_PUBLISH") != commit
                or os.environ.get("SUPERRAN_CLOUD_BASE") != base):
            raise RuntimeError("主干只能通过 publish 和匹配完整 SHA 的合并回执发布。")
        validate_receipt(commit, base)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    p = sub.add_parser("doctor"); p.add_argument("--online", action="store_true")
    p = sub.add_parser("start"); p.add_argument("branch"); p.add_argument("path")
    p = sub.add_parser("submit"); p.add_argument("--base", required=True)
    p = sub.add_parser("fetch-candidate"); p.add_argument("branch"); p.add_argument("--sha", required=True)
    p = sub.add_parser("merge"); p.add_argument("branch"); p.add_argument("--sha", required=True); p.add_argument("--base", required=True); p.add_argument("--initial", action="store_true")
    p = sub.add_parser("publish"); p.add_argument("--sha", required=True); p.add_argument("--expected-remote", required=True); p.add_argument("--initial", action="store_true")
    p = sub.add_parser("check-push"); p.add_argument("remote"); p.add_argument("url")
    args = parser.parse_args()
    try:
        if args.command != "check-push":
            print(f"正在执行 {args.command}，完成后输出实际读回状态。", file=sys.stderr, flush=True)
        globals()[args.command.replace("-", "_") if args.command != "init" else "init_repo"](args)
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
