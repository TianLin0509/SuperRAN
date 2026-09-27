"""Exercise the merge gate against disposable Git repositories, never production.

This complements run_test_matrix.py (all simulation test files). It needs only
Python, Git, and Git sh; fixtures have no network remotes or reparse points.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILES = ("pyproject.toml", "src/superran/__init__.py")


def write_version_fixture(root, version, newline="\n"):
    """最小版的两处版本文件，结构与真实仓库一致（含不应被改的 target-version）。"""
    files = {
        "pyproject.toml": ['[project]', 'name = "superran"', f'version = "{version}"', '',
                           '[tool.ruff]', 'target-version = "py310"', ''],
        "src/superran/__init__.py": ['"""fixture."""', f'__version__ = "{version}"', ''],
    }
    for rel, lines in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(newline.join(lines))


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
        for path in ("scripts/merge_task.py", "scripts/bump_version.py",
                     ".githooks/pre-commit", ".githooks/pre-push"):
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        (self.repo / ".agents").mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "src" / "fixture.txt").write_text("baseline\n", encoding="utf-8")
        write_version_fixture(self.repo, "0.1.0")
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

    def enable_versioning(self, command=None):
        """在主干上提交 versionBump 配置；任务分支仍不碰版本号。"""
        self.config["versionFiles"] = list(VERSION_FILES)
        self.config["versionBump"] = [command or f'"{sys.executable}" scripts/bump_version.py']
        self.write_config()
        self.git("add", ".agents/project.json")
        self.git("-c", "core.hooksPath=", "commit", "-m", "enable versioning")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()

    def versions_at(self, rev=None):
        out = []
        for rel in VERSION_FILES:
            text = (self.git("show", f"{rev}:{rel}").stdout if rev
                    else (self.repo / rel).read_text(encoding="utf-8"))
            out.append(re.search(r'^(?:version|__version__)\s*=\s*"([^"]+)"', text, re.M).group(1))
        return out

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

    def test_new_staged_work_survives_successful_checks(self):
        self.assert_new_staged_work_survives(0)

    def test_new_staged_work_survives_failed_checks(self):
        self.assert_new_staged_work_survives(7)

    def assert_new_staged_work_survives(self, exit_code):
        self.amend_check(
            "from pathlib import Path\nimport subprocess\n"
            "Path('concurrent.txt').write_text('owned by another task',encoding='utf-8')\n"
            "subprocess.run(['git','add','concurrent.txt'],check=True)\n"
            f"raise SystemExit({exit_code})\n")
        result = self.merge()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.base)
        self.assertEqual(self.git("rev-parse", "MERGE_HEAD").stdout.strip(), self.head)
        self.assertEqual((self.repo / "concurrent.txt").read_text(encoding="utf-8"),
                         "owned by another task")
        self.assertEqual(self.git("show", ":concurrent.txt").stdout, "owned by another task")
        self.assertIn("未执行 merge --abort", result.stdout)

    def test_unstaged_work_survives_failed_checks(self):
        self.amend_check(
            "from pathlib import Path\n"
            "Path('src/fixture.txt').write_text('concurrent tracked edit',encoding='utf-8')\n"
            "raise SystemExit(7)\n")
        result = self.merge()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual((self.repo / "src/fixture.txt").read_text(encoding="utf-8"),
                         "concurrent tracked edit")
        self.assertEqual(self.git("rev-parse", "MERGE_HEAD").stdout.strip(), self.head)

    def test_merge_conflict_is_preserved(self):
        (self.repo / "src/fixture.txt").write_text("trunk changed\n", encoding="utf-8")
        self.git("add", "src/fixture.txt")
        self.git("-c", "core.hooksPath=", "commit", "-m", "conflicting trunk")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        result = self.merge()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.base)
        self.assertTrue(self.git("ls-files", "--unmerged").stdout)
        self.assertEqual(self.git("rev-parse", "MERGE_HEAD").stdout.strip(), self.head)

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

    def test_version_bump_lands_in_reviewed_merge_commit(self):
        self.enable_versioning()
        result = self.merge()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parents = self.git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[1:], [self.base, self.head])
        self.assertEqual(self.versions_at("HEAD"), ["0.1.1", "0.1.1"])
        self.assertEqual(self.versions_at(self.head), ["0.1.0", "0.1.0"])
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        changed = set(self.git("diff", "--name-only", self.base, "HEAD").stdout.split())
        self.assertEqual(changed, {"src/fixture.txt", *VERSION_FILES})

    def test_version_bump_runs_before_checks_and_dry_run_restores(self):
        self.enable_versioning()
        self.amend_check(
            "import re\nfrom pathlib import Path\n"
            "text = Path('pyproject.toml').read_text(encoding='utf-8')\n"
            "assert re.search(r'^version = \"0.1.1\"', text, re.M), text\n")
        result = self.merge("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_clean_base()
        self.assertEqual(self.versions_at(), ["0.1.0", "0.1.0"])

    def test_failed_version_bump_rolls_back(self):
        self.enable_versioning(f'"{sys.executable}" -c "raise SystemExit(3)"')
        result = self.merge()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("抬版本号失败", result.stdout)
        self.assert_clean_base()

    def test_version_bump_touching_other_files_is_preserved_not_committed(self):
        stray = Path(self.temp.name) / "stray.py"
        stray.write_text("open('src/fixture.txt', 'w').write('stray')\n", encoding="utf-8")
        self.enable_versioning(
            f'"{sys.executable}" scripts/bump_version.py && "{sys.executable}" "{stray}"')
        result = self.merge()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("versionFiles 以外", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.base)
        self.assertEqual(self.git("rev-parse", "MERGE_HEAD").stdout.strip(), self.head)

    def test_version_config_must_be_paired(self):
        self.config["versionBump"] = [f'"{sys.executable}" scripts/bump_version.py']
        self.write_config()
        result = self.merge()
        self.assertEqual(result.returncode, 2)
        self.assertIn("versionFiles", result.stdout)

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


class BumpVersionContract(unittest.TestCase):
    """bump_version.py 本身：两处同步、只改版本那一行、认不出就拒绝。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="20260927-superran-bump-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location("bump_version_under_test",
                                                      ROOT / "scripts/bump_version.py")
        self.bump = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bump)

    def call(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.bump.main(list(argv), root=self.root)
        return code, stdout.getvalue() + stderr.getvalue()

    def snapshot(self):
        return {rel: (self.root / rel).read_bytes() for rel in VERSION_FILES}

    def test_patch_bump_keeps_both_files_in_step_and_rolls_digits(self):
        write_version_fixture(self.root, "0.1.9")
        self.assertEqual(self.call(), (0, "版本号 0.1.9 → 0.1.10\n"))
        self.assertEqual(self.bump.read_versions(self.root),
                         dict.fromkeys(VERSION_FILES, "0.1.10"))

    def test_only_the_version_line_changes_and_line_endings_survive(self):
        write_version_fixture(self.root, "1.2.3", newline="\r\n")
        before = self.snapshot()
        self.assertEqual(self.call()[0], 0)
        for rel, old in before.items():
            new = (self.root / rel).read_bytes()
            self.assertEqual(new, old.replace(b'"1.2.3"', b'"1.2.4"'), rel)
        self.assertIn(b'target-version = "py310"', (self.root / "pyproject.toml").read_bytes())

    def test_set_explicit_version(self):
        write_version_fixture(self.root, "0.1.7")
        self.assertEqual(self.call("--set", "0.2.0")[0], 0)
        self.assertEqual(self.bump.current_version(self.root), "0.2.0")

    def test_print_and_check_do_not_write(self):
        write_version_fixture(self.root, "0.1.0")
        before = self.snapshot()
        self.assertEqual(self.call("--print"), (0, "0.1.0\n"))
        self.assertEqual(self.call("--check")[0], 0)
        self.assertEqual(self.snapshot(), before)

    def test_refusals_leave_files_untouched(self):
        cases = {
            "mismatch": lambda: (self.root / "src/superran/__init__.py").write_text(
                '__version__ = "0.1.1"\n', encoding="utf-8"),
            "duplicate": lambda: (self.root / "pyproject.toml").write_text(
                '[project]\nversion = "0.1.0"\nversion = "0.1.0"\n', encoding="utf-8"),
            "missing": lambda: (self.root / "pyproject.toml").write_text(
                '[project]\nname = "superran"\n', encoding="utf-8"),
            "not_semver": lambda: write_version_fixture(self.root, "0.1.0rc1"),
        }
        for name, corrupt in cases.items():
            with self.subTest(name):
                write_version_fixture(self.root, "0.1.0")
                corrupt()
                before = self.snapshot()
                code, out = self.call()
                self.assertEqual(code, 1, out)
                self.assertEqual(self.snapshot(), before)
        write_version_fixture(self.root, "0.1.0")
        before = self.snapshot()
        for bad in ("0.2", "0.1.0", "v0.2.0"):
            with self.subTest(set=bad):
                self.assertEqual(self.call("--set", bad)[0], 1)
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
