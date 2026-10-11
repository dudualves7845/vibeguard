"""Integration tests for GitIntegration and Git Hook functionality."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from vibeguard.git_utils import GitIntegration


class TestGitIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # Initialize real git repo in temp dir
        subprocess.run(["git", "init"], cwd=str(self.repo_dir), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "config", "user.email", "test@vibeguard.com"], cwd=str(self.repo_dir), check=True)
        subprocess.run(["git", "config", "user.name", "Vibe Tester"], cwd=str(self.repo_dir), check=True)

        self.git = GitIntegration(str(self.repo_dir))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_git_repo(self):
        self.assertTrue(self.git.is_git_repo())
        non_git = GitIntegration(str(self.repo_dir / "non_existent_folder"))
        self.assertFalse(non_git.is_git_repo())

    def test_install_and_uninstall_pre_commit_hook(self):
        hook_path = self.git.install_pre_commit_hook()
        self.assertTrue(hook_path.exists())
        self.assertTrue(os.access(str(hook_path), os.X_OK))

        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("vibeguard check --staged", content)

        # Test uninstall
        removed = self.git.uninstall_pre_commit_hook()
        self.assertTrue(removed)
        self.assertFalse(hook_path.exists())

    def test_get_staged_and_changed_files(self):
        # Create initial commit
        f1 = self.repo_dir / "file1.py"
        f1.write_text("print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "file1.py"], cwd=str(self.repo_dir), check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(self.repo_dir), check=True)

        # Before staging anything
        self.assertEqual(self.git.get_staged_files(), [])

        # Modify f1 and stage it
        f1.write_text("print('hello updated')\n", encoding="utf-8")
        subprocess.run(["git", "add", "file1.py"], cwd=str(self.repo_dir), check=True)

        # Create new unstaged file
        f2 = self.repo_dir / "file2.py"
        f2.write_text("print('untracked')\n", encoding="utf-8")

        staged = self.git.get_staged_files()
        self.assertIn("file1.py", staged)
        self.assertNotIn("file2.py", staged)

        changed = self.git.get_changed_files()
        self.assertIn("file1.py", changed)
        self.assertIn("file2.py", changed)

    def test_deleted_files_tracked_in_staged_and_changed(self):
        f1 = self.repo_dir / "file1.py"
        f1.write_text("print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "file1.py"], cwd=str(self.repo_dir), check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(self.repo_dir), check=True)

        # Delete file unstaged
        f1.unlink()
        changed = self.git.get_changed_files()
        self.assertIn("file1.py", changed)

        # Stage deletion
        subprocess.run(["git", "add", "-u"], cwd=str(self.repo_dir), check=True)
        staged = self.git.get_staged_files()
        self.assertIn("file1.py", staged)

    def test_git_diff_with_merge_conflict(self):
        f = self.repo_dir / "conflict.py"
        f.write_text("initial\n", encoding="utf-8")
        subprocess.run(["git", "add", "conflict.py"], cwd=str(self.repo_dir), check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(self.repo_dir), check=True)

        subprocess.run(["git", "checkout", "-b", "feature"], cwd=str(self.repo_dir), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        f.write_text("feature branch change\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-am", "feature change"], cwd=str(self.repo_dir), check=True)

        # Switch back to main/master
        subprocess.run(["git", "checkout", "-"], cwd=str(self.repo_dir), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        f.write_text("master branch change\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-am", "master change"], cwd=str(self.repo_dir), check=True)

        # Merge feature causing conflict
        subprocess.run(["git", "merge", "feature"], cwd=str(self.repo_dir), capture_output=True)

        # Verify get_changed_files works safely even during unresolved conflict state
        changed = self.git.get_changed_files()
        self.assertIn("conflict.py", changed)

    def test_git_root_discovery_from_subdirectory(self):
        # Create nested directory
        sub = self.repo_dir / "src" / "deep"
        sub.mkdir(parents=True)
        sub_file = sub / "nested.py"
        sub_file.write_text("def nested(): pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/deep/nested.py"], cwd=str(self.repo_dir), check=True)

        # Initialize GitIntegration from the subdirectory
        sub_git = GitIntegration(str(sub))
        self.assertEqual(sub_git.get_git_root(), self.repo_dir.resolve())

        staged = sub_git.get_staged_files()
        self.assertIn("src/deep/nested.py", staged)

        # Also add untracked file
        untracked = sub / "untracked.py"
        untracked.write_text("print('test')\n", encoding="utf-8")
        changed = sub_git.get_changed_files()
        self.assertIn("src/deep/untracked.py", changed)

    def test_custom_core_hooks_path_respected(self):
        # Configure core.hooksPath to a custom directory
        custom_hooks_dir = self.repo_dir / ".custom_githooks"
        subprocess.run(["git", "config", "core.hooksPath", ".custom_githooks"], cwd=str(self.repo_dir), check=True)
        
        hook_path = self.git.install_pre_commit_hook()
        self.assertEqual(hook_path.resolve(), (custom_hooks_dir / "pre-commit").resolve())
        self.assertTrue(hook_path.exists())

        # Uninstall removes from the custom hooks directory
        self.assertTrue(self.git.uninstall_pre_commit_hook())
        self.assertFalse(hook_path.exists())

    def test_rename_tracking_in_porcelain(self):
        f1 = self.repo_dir / "orig.py"
        f1.write_text("print('original')\n", encoding="utf-8")
        subprocess.run(["git", "add", "orig.py"], cwd=str(self.repo_dir), check=True)
        subprocess.run(["git", "commit", "-m", "commit orig"], cwd=str(self.repo_dir), check=True)

        subprocess.run(["git", "mv", "orig.py", "renamed.py"], cwd=str(self.repo_dir), check=True)
        staged = self.git.get_staged_files()
        self.assertIn("orig.py", staged)
        self.assertIn("renamed.py", staged)

    def _fake_vibeguard(self, exit_code=0):
        bindir = self.repo_dir / "fake tools with spaces"
        bindir.mkdir(exist_ok=True)
        executable = bindir / "vibeguard"
        executable.write_text("#!/bin/sh\nexit %d\n" % exit_code)
        executable.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        return env

    def test_existing_hook_is_chained_and_restored_idempotently(self):
        hooks = self.git.get_hooks_dir()
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        original = '#!/bin/sh\nprintf custom > original-ran\nexit 0\n'
        hook.write_text(original)
        hook.chmod(0o750)
        self.git.install_pre_commit_hook()
        self.git.install_pre_commit_hook()
        result = subprocess.run([str(hook)], cwd=str(self.repo_dir), env=self._fake_vibeguard(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo_dir / "original-ran").read_text(), "custom")
        self.assertTrue(self.git.uninstall_pre_commit_hook())
        self.assertEqual(hook.read_text(), original)
        self.assertEqual(hook.stat().st_mode & 0o777, 0o750)

    def test_existing_hook_rejection_still_blocks_commit(self):
        hooks = self.git.get_hooks_dir()
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 9\n")
        hook.chmod(0o755)
        self.git.install_pre_commit_hook()
        result = subprocess.run([str(hook)], cwd=str(self.repo_dir), env=self._fake_vibeguard(), capture_output=True)
        self.assertEqual(result.returncode, 9)

    def test_hook_failure_prints_remediation_and_keeps_exit_code(self):
        hook = self.git.install_pre_commit_hook()
        result = subprocess.run([str(hook)], cwd=str(self.repo_dir), env=self._fake_vibeguard(7), capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)
        self.assertIn("--prompt", result.stdout + result.stderr)

    def test_hook_runs_local_binary_under_path_with_spaces(self):
        bindir = self.repo_dir / "bin"
        bindir.mkdir()
        executable = bindir / "vibeguard"
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > hook-args\n')
        executable.chmod(0o755)
        hook = self.git.install_pre_commit_hook()
        # Restrict PATH to system tools so the local launcher is selected.
        env = dict(os.environ, PATH="/usr/bin:/bin")
        result = subprocess.run([str(hook)], cwd=str(self.repo_dir), env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo_dir / "hook-args").read_text(), "check\n--staged\n")

    def test_uninstall_does_not_remove_unrelated_hook_mentioning_vibeguard(self):
        hooks = self.git.get_hooks_dir()
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        original = "#!/bin/sh\n# Custom checks, including VibeGuard\nexit 0\n"
        hook.write_text(original)
        self.assertFalse(self.git.is_hook_installed())
        self.assertFalse(self.git.uninstall_pre_commit_hook())
        self.assertEqual(hook.read_text(), original)

    def test_custom_hooks_path_from_subdirectory_uses_git_root(self):
        subprocess.run(["git", "config", "core.hooksPath", "hooks with spaces"], cwd=str(self.repo_dir), check=True)
        subdir = self.repo_dir / "nested"
        subdir.mkdir()
        hook = GitIntegration(str(subdir)).install_pre_commit_hook()
        self.assertEqual(hook, self.repo_dir / "hooks with spaces" / "pre-commit")


if __name__ == "__main__":
    unittest.main()

