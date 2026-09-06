"""Exercise the merge gate against disposable Git repositories, never production.

This complements run_test_matrix.py (all simulation test files). It needs only
Python, Git, and Git sh; fixtures have no network remotes or reparse points.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkflowContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="20260906-superran-workflow-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "Workflow fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Workflow fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        self.env.pop("HUB_ALLOW_MAIN_COMMIT", None)
        self.env.pop("HUB_ALLOW_TRUNK_PUSH", None)
        for path in ("scripts/merge_task.py", ".githooks/pre-commit", ".githooks/pre-push"):
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        (self.repo / ".agents").mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "src" / "fixture.txt").write_text("baseline\n", encoding="utf-8")
        (self.repo / "check.py").write_text("pass\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self.config = {"name": "fixture", "trunk": "develop",
                       "test": [f'"{sys.executable}" check.py'], "afterMerge": []}
        self.write_config()
        self.git("init", "-b", "develop")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-b", "task")
        (self.repo / "src" / "fixture.txt").write_text("candidate\n", encoding="utf-8")
        self.git("add", "src/fixture.txt")
        self.git("commit", "-m", "candidate")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "develop")
        self.git("config", "core.hooksPath", ".githooks")

    def run_cmd(self, args, *, cwd=None, check=True, input=None):
        result = subprocess.run(args, cwd=cwd or self.repo, env=self.env,
                                input=input, capture_output=True, text=True,
                                encoding="utf-8", errors="strict", timeout=45)
        if check and result.returncode:
            self.fail(f"{args}: {result.returncode}\n{result.stdout}\n{result.stderr}")
        return result

    def git(self, *args, **kwargs):
        return self.run_cmd(["git", *args], **kwargs)

    def write_config(self):
        (self.repo / ".agents/project.json").write_text(
            json.dumps(self.config), encoding="utf-8")

    def amend_check(self, source):
        self.git("checkout", "task")
        (self.repo / "check.py").write_text(source, encoding="utf-8")
        self.git("add", "check.py")
        # This fixture's primary worktree is deliberately protected too.
        self.git("-c", "core.hooksPath=", "commit", "-m", "check behavior")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "develop")

    def merge(self, *extra, script=None):
        return self.run_cmd(
            [sys.executable, str(script or self.repo / "scripts/merge_task.py"), "task",
             "--expected-head", self.head, "--expected-trunk", self.base, *extra], check=False)

    def assert_clean_base(self):
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.base)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_dry_run_restores_exact_state(self):
        result = self.merge("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_clean_base()

    def test_real_merge_is_local_and_has_reviewed_second_parent(self):
        remote = Path(self.temp.name) / "remote.git"
        self.git("clone", "--bare", str(self.repo), str(remote))
        self.git("remote", "add", "origin", str(remote))
        result = self.merge()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parents = self.git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[1:], [self.base, self.head])
        self.assertEqual(self.git("--git-dir", str(remote), "rev-parse", "develop").stdout.strip(), self.base)
        self.assertFalse((self.repo / ".git/FETCH_HEAD").exists())
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_stale_head_and_stale_trunk_rejected(self):
        for flag in ("--expected-head", "--expected-trunk"):
            result = self.merge(flag, "0" * 40)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assert_clean_base()

    def test_failure_returns_nonzero_and_rolls_back(self):
        self.amend_check("raise SystemExit(7)\n")
        result = self.merge()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_clean_base()

    def test_dirty_staged_and_untracked_files_are_preserved(self):
        staged = self.repo / "unrelated.txt"
        staged.write_text("someone else's work", encoding="utf-8")
        self.git("add", "unrelated.txt")
        (self.repo / "另一任务.txt").write_text("不能丢", encoding="utf-8")
        before = self.git("status", "--porcelain").stdout
        tree = self.git("write-tree").stdout
        self.assertEqual(self.merge().returncode, 2)
        self.assertEqual(self.git("status", "--porcelain").stdout, before)
        self.assertEqual(self.git("write-tree").stdout, tree)
        self.assertEqual(staged.read_text(encoding="utf-8"), "someone else's work")

    def test_worktree_cannot_merge(self):
        worktree = Path(self.temp.name) / "author"
        self.git("worktree", "add", str(worktree), "task")
        result = self.merge(script=worktree / "scripts/merge_task.py")
        self.assertEqual(result.returncode, 2)
        self.assertIn("必须在主工作目录", result.stdout)
        self.assert_clean_base()

    def test_empty_gate_rejected(self):
        self.config["test"] = []
        self.write_config()
        result = self.merge()
        self.assertEqual(result.returncode, 2)
        self.assertIn("非空测试命令", result.stdout)

    def test_import_path_and_utf8_are_set_for_child(self):
        self.env["PYTHONPATH"] = "wrong-checkout"
        self.amend_check(
            "import os\nfrom pathlib import Path\n"
            "assert Path(os.environ['PYTHONPATH']).resolve() == Path('src').resolve()\n"
            "assert os.environ['PYTHONIOENCODING'] == 'utf-8'\nprint('中文验证')\n")
        result = self.merge("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("中文验证", result.stdout)
        self.assert_clean_base()

    def test_candidate_movement_during_checks_rejected(self):
        self.amend_check(
            "import subprocess\nsubprocess.run(['git','update-ref','refs/heads/task',"
            + repr(self.base) + "],check=True)\n")
        result = self.merge()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_clean_base()

    def test_merge_lock_rejects_concurrent_runner(self):
        lock = open(self.repo / ".git/hub-merge-task.lock", "w+b")
        try:
            lock.write(b"0")
            lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.merge().returncode, 2)
            self.assert_clean_base()
        finally:
            lock.close()

    def test_hooks_protect_main_and_allow_worktree_and_topic(self):
        sh = shutil.which("sh") or "C:/Program Files/Git/bin/sh.exe"
        worktree = Path(self.temp.name) / "author"
        self.git("worktree", "add", str(worktree), "task")
        for cwd, expected in ((self.repo, 1), (worktree, 0)):
            result = self.run_cmd([sh, ".githooks/pre-commit"], cwd=cwd, check=False)
            self.assertEqual(result.returncode, expected, result.stderr)
        for branch, expected in (("develop", 1), ("main", 1), ("master", 1), ("task", 0)):
            result = self.run_cmd([sh, ".githooks/pre-push", "origin", "fixture"], check=False,
                                  input=f"refs/heads/task a1 refs/heads/{branch} b2\n")
            self.assertEqual(result.returncode, expected, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
