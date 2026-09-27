"""Two independent local clones + bare server exercise cloud Git semantics.

No production credentials, network or simulation runs are used here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class CloudWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="superran-cloud-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = self.root / "server.git"
        self.author = self.root / "author"
        self.reviewer = self.root / "reviewer"
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Cloud fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Cloud fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                    "PYTHONIOENCODING": "utf-8"}
        for name in ("HUB_ALLOW_MAIN_COMMIT", "HUB_ALLOW_TRUNK_PUSH", "SUPERRAN_CLOUD_PUBLISH", "SUPERRAN_CLOUD_BASE"):
            self.env.pop(name, None)
        self.author.mkdir()
        self.cmd(self.author, "git", "init", "-b", "develop")
        for name in ("scripts/agent_repo.py", "scripts/merge_task.py", ".githooks/pre-push", ".githooks/pre-commit"):
            path = self.author / name
            path.parent.mkdir(exist_ok=True)
            shutil.copyfile(SOURCE / name, path)
        (self.author / ".agents").mkdir()
        (self.author / ".agents/project.json").write_text(json.dumps({
            "trunk": "develop", "test": [f'"{sys.executable}" check.py'], "afterMerge": [],
            "cloudRepository": {"remote": "origin", "url": str(self.server)}}), encoding="utf-8")
        (self.author / "check.py").write_text("assert True\n", encoding="utf-8")
        self.cmd(self.author, "git", "add", ".")
        self.cmd(self.author, "git", "commit", "-m", "base")
        self.base = self.cmd(self.author, "git", "rev-parse", "HEAD").stdout.strip()
        self.cmd(self.root, "git", "clone", "--bare", str(self.author), str(self.server))
        self.cmd(self.root, "git", "clone", str(self.server), str(self.reviewer))
        self.cmd(self.author, "git", "remote", "add", "origin", str(self.server))
        self.agent(self.author, "init")
        self.agent(self.reviewer, "init")
        self.task = self.root / "task"
        self.agent(self.author, "start", "feat/fixture", str(self.task))
        (self.task / "feature.txt").write_text("candidate", encoding="utf-8")
        self.cmd(self.task, "git", "add", "feature.txt")
        self.cmd(self.task, "git", "commit", "-m", "feature")
        self.candidate = self.cmd(self.task, "git", "rev-parse", "HEAD").stdout.strip()

    def cmd(self, cwd, *args, ok=True, env=None):
        result = subprocess.run(args, cwd=cwd, env={**self.env, **(env or {})}, text=True,
                                encoding="utf-8", capture_output=True, timeout=90)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def agent(self, cwd, *args, **kwargs):
        return self.cmd(cwd, sys.executable, "scripts/agent_repo.py", *args, **kwargs)

    def submit_fetch(self):
        self.agent(self.task, "submit", "--base", self.base)
        result = self.agent(self.reviewer, "fetch-candidate", "feat/fixture", "--sha", self.candidate)
        return result.stdout.strip()

    def merged(self):
        branch = self.submit_fetch()
        self.agent(self.reviewer, "merge", branch, "--sha", self.candidate, "--base", self.base)
        return self.cmd(self.reviewer, "git", "rev-parse", "HEAD").stdout.strip()

    def test_cross_clone_submission_review_merge_publish(self):
        merged = self.merged()
        self.agent(self.reviewer, "publish", "--sha", merged, "--expected-remote", self.base)
        actual = self.cmd(self.root, "git", "--git-dir", str(self.server), "rev-parse", "develop").stdout.strip()
        self.assertEqual(actual, merged)
        self.agent(self.reviewer, "publish", "--sha", merged, "--expected-remote", self.base)

    def test_stale_candidate_and_short_sha_rejected(self):
        self.agent(self.task, "submit", "--base", self.base)
        for value in (self.base, self.candidate[:12]):
            self.agent(self.reviewer, "fetch-candidate", "feat/fixture", "--sha", value, ok=False)

    def test_raw_push_and_legacy_bypass_rejected(self):
        self.cmd(self.task, "git", "push", "origin", "HEAD:refs/heads/develop", ok=False,
                 env={"HUB_ALLOW_TRUNK_PUSH": "1"})
        self.cmd(self.task, "git", "push", "origin", "HEAD:refs/heads/main", ok=False)

    def test_missing_receipt_and_modified_sha_rejected(self):
        self.agent(self.reviewer, "publish", "--sha", self.base, "--expected-remote", self.base, ok=False)
        merged = self.merged()
        receipt = self.reviewer / ".git/superran-cloud-receipts" / f"{merged}.json"
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data["candidate"] = self.base
        receipt.write_text(json.dumps(data), encoding="utf-8")
        self.agent(self.reviewer, "publish", "--sha", merged, "--expected-remote", self.base, ok=False)

    def test_dirty_author_and_stale_remote_rejected(self):
        (self.task / "dirty.txt").write_text("belongs to author", encoding="utf-8")
        self.agent(self.task, "submit", "--base", self.base, ok=False)
        self.assertEqual((self.task / "dirty.txt").read_text(), "belongs to author")
        self.agent(self.reviewer, "merge", "develop", "--sha", self.base, "--base", "0" * 40, ok=False)

    def test_wrong_remote_rejected(self):
        self.cmd(self.task, "git", "remote", "set-url", "--push", "origin", "https://github.com/example/repo.git")
        self.agent(self.task, "submit", "--base", self.base, ok=False)

    def test_initial_empty_server_requires_explicit_flag_and_gate(self):
        branch = self.submit_fetch()
        # Delete only this disposable fixture's develop ref to model empty trunk.
        self.cmd(self.root, "git", "--git-dir", str(self.server), "update-ref", "-d", "refs/heads/develop")
        self.agent(self.reviewer, "merge", branch, "--sha", self.candidate, "--base", self.base, ok=False)
        self.agent(self.reviewer, "merge", branch, "--sha", self.candidate, "--base", self.base, "--initial")
        merged = self.cmd(self.reviewer, "git", "rev-parse", "HEAD").stdout.strip()
        self.agent(self.reviewer, "publish", "--sha", merged, "--expected-remote", "EMPTY", ok=False)
        self.agent(self.reviewer, "publish", "--sha", merged, "--expected-remote", "EMPTY", "--initial")

    def test_failed_gate_never_writes_receipt(self):
        (self.task / "check.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
        self.cmd(self.task, "git", "add", "check.py")
        self.cmd(self.task, "git", "commit", "-m", "failing check")
        self.candidate = self.cmd(self.task, "git", "rev-parse", "HEAD").stdout.strip()
        branch = self.submit_fetch()
        self.agent(self.reviewer, "merge", branch, "--sha", self.candidate, "--base", self.base, ok=False)
        self.assertFalse((self.reviewer / ".git/superran-cloud-receipts").exists())
        self.assertEqual(self.cmd(self.reviewer, "git", "rev-parse", "HEAD").stdout.strip(), self.base)

    def test_remote_advance_after_local_merge_rejected(self):
        merged = self.merged()
        # Server moves to another descendant before publication.
        self.cmd(self.root, "git", "--git-dir", str(self.server), "update-ref", "refs/heads/develop", self.candidate)
        self.agent(self.reviewer, "publish", "--sha", merged, "--expected-remote", self.base, ok=False)
        actual = self.cmd(self.root, "git", "--git-dir", str(self.server), "rev-parse", "develop").stdout.strip()
        self.assertEqual(actual, self.candidate)

    def test_github_history_migration_keeps_commits_and_blocks_push(self):
        before = self.cmd(self.author, "git", "rev-parse", "HEAD").stdout
        self.cmd(self.author, "git", "remote", "set-url", "origin", "https://github.com/example/SuperRAN.git")
        self.agent(self.author, "init")
        self.assertEqual(self.cmd(self.author, "git", "rev-parse", "HEAD").stdout, before)
        actual = self.cmd(self.author, "git", "remote", "get-url", "--push", "github-archive").stdout.strip()
        self.assertEqual(actual, "disabled://github-archive")
        actual = self.cmd(self.author, "git", "config", "--get", "branch.develop.remote").stdout.strip()
        self.assertEqual(actual, "origin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
