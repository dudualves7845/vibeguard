"""Real filesystem/Git regressions for safe checkpoint restoration."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from vibeguard.rewind import CheckpointManager


class RewindRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibeguard rewind ")
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=str(self.root))

    def init_git(self):
        self.git("init", "-q")
        self.git("config", "user.name", "Regression")
        self.git("config", "user.email", "regression@example.invalid")
        (self.root / "base.txt").write_text("base\n")
        (self.root / "keep.txt").write_text("keep\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")

    def checkpoint(self, root=None):
        manager = CheckpointManager(str(root or self.root))
        return manager, manager.create_checkpoint(evaluate_drift=False)

    def test_rewind_removes_new_staged_and_committed_files(self):
        self.init_git()
        manager, checkpoint = self.checkpoint()
        (self.root / "committed.txt").write_text("new committed")
        self.git("add", "committed.txt")
        self.git("commit", "-qm", "later commit")
        head = self.git("rev-parse", "HEAD")
        (self.root / "staged.txt").write_text("new staged")
        self.git("add", "staged.txt")
        result = manager.rewind_to(checkpoint.id)
        self.assertFalse((self.root / "committed.txt").exists())
        self.assertFalse((self.root / "staged.txt").exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        manager.rewind_to(result["safety_checkpoint"]["id"])
        self.assertEqual((self.root / "committed.txt").read_text(), "new committed")
        self.assertEqual(self.git("show", ":staged.txt"), b"new staged")

    def test_rewind_restores_absence_captured_in_checkpoint(self):
        self.init_git()
        (self.root / "base.txt").unlink()
        manager, checkpoint = self.checkpoint()
        (self.root / "base.txt").write_text("recreated")
        manager.rewind_to(checkpoint.id)
        self.assertFalse((self.root / "base.txt").exists())

    def test_rewind_preserves_partial_staging_and_safety_index(self):
        self.init_git()
        (self.root / "base.txt").write_text("staged\n")
        self.git("add", "base.txt")
        (self.root / "base.txt").write_text("working\n")
        index_before = self.git("diff", "--cached", "--binary")
        manager, checkpoint = self.checkpoint()
        self.assertEqual(self.git("diff", "--cached", "--binary"), index_before)
        (self.root / "base.txt").write_text("safety staged\n")
        self.git("add", "base.txt")
        (self.root / "base.txt").write_text("safety working\n")
        result = manager.rewind_to(checkpoint.id)
        self.assertEqual(self.git("show", ":base.txt"), b"staged\n")
        self.assertEqual((self.root / "base.txt").read_text(), "working\n")
        manager.rewind_to(result["safety_checkpoint"]["id"])
        self.assertEqual(self.git("show", ":base.txt"), b"safety staged\n")
        self.assertEqual((self.root / "base.txt").read_text(), "safety working\n")

    def test_git_subdirectory_uses_repository_checkpoint_storage(self):
        self.init_git()
        sub = self.root / "src"
        sub.mkdir()
        manager, checkpoint = self.checkpoint(sub)
        (self.root / "base.txt").write_text("changed")
        manager.rewind_to(checkpoint.id)
        self.assertEqual((self.root / "base.txt").read_text(), "base\n")
        self.assertEqual(manager.root, self.root)
        self.assertFalse((sub / ".vibeguard").exists())

    def test_linked_worktree_checkpoint_and_rewind(self):
        self.init_git()
        worktree = Path(self.temp.name) / "linked worktree"
        self.git("worktree", "add", "-qb", "linked", str(worktree))
        manager, checkpoint = self.checkpoint(worktree)
        (worktree / "base.txt").write_text("changed")
        manager.rewind_to(checkpoint.id)
        self.assertEqual((worktree / "base.txt").read_text(), "base\n")

    def test_rewind_handles_unicode_and_escaped_names(self):
        self.init_git()
        manager, checkpoint = self.checkpoint()
        names = ['não.py', ' line\nwith "quotes".py ', 'tab\t.py']
        for name in names:
            (self.root / name).write_text("new")
        result = manager.rewind_to(checkpoint.id)
        self.assertEqual(set(result["removed_files"]), set(names))
        for name in names:
            self.assertFalse((self.root / name).exists())
        manager.rewind_to(result["safety_checkpoint"]["id"])
        for name in names:
            self.assertEqual((self.root / name).read_text(), "new")

    def test_non_git_rewind_does_not_follow_file_symlink(self):
        (self.root / "config.txt").write_text("snapshot")
        manager, checkpoint = self.checkpoint()
        external = Path(self.temp.name) / "external.txt"
        external.write_text("external")
        (self.root / "config.txt").unlink()
        (self.root / "config.txt").symlink_to(external)
        result = manager.rewind_to(checkpoint.id)
        self.assertEqual(external.read_text(), "external")
        self.assertFalse((self.root / "config.txt").is_symlink())
        self.assertEqual((self.root / "config.txt").read_text(), "snapshot")
        manager.rewind_to(result["safety_checkpoint"]["id"])
        self.assertTrue((self.root / "config.txt").is_symlink())
        self.assertEqual(os.readlink(str(self.root / "config.txt")), str(external))

    def test_non_git_rewind_does_not_follow_parent_symlink(self):
        (self.root / "nested").mkdir()
        (self.root / "nested" / "config.txt").write_text("snapshot")
        manager, checkpoint = self.checkpoint()
        external = Path(self.temp.name) / "external"
        external.mkdir()
        (external / "config.txt").write_text("external")
        shutil.rmtree(str(self.root / "nested"))
        (self.root / "nested").symlink_to(external, target_is_directory=True)
        manager.rewind_to(checkpoint.id)
        self.assertEqual((external / "config.txt").read_text(), "external")
        self.assertFalse((self.root / "nested").is_symlink())
        self.assertEqual((self.root / "nested" / "config.txt").read_text(), "snapshot")

    def test_non_git_captures_dotgithub_gitignore_and_symlinks(self):
        (self.root / ".github").mkdir()
        (self.root / ".github" / "workflow.yml").write_text("workflow")
        (self.root / ".gitignore").write_text("ignored/")
        (self.root / "missing-link").symlink_to("missing-target")
        manager, checkpoint = self.checkpoint()
        (self.root / ".github" / "workflow.yml").write_text("changed")
        (self.root / ".gitignore").unlink()
        (self.root / "missing-link").unlink()
        manager.rewind_to(checkpoint.id)
        self.assertEqual((self.root / ".github" / "workflow.yml").read_text(), "workflow")
        self.assertEqual((self.root / ".gitignore").read_text(), "ignored/")
        self.assertEqual(os.readlink(str(self.root / "missing-link")), "missing-target")

    def test_git_checkpoint_and_restore_do_not_follow_parent_symlink(self):
        self.init_git()
        (self.root / "nested").mkdir()
        (self.root / "nested" / "config.txt").write_text("snapshot")
        self.git("add", "nested")
        self.git("commit", "-qm", "nested")
        manager, checkpoint = self.checkpoint()
        external = Path(self.temp.name) / "external"
        external.mkdir()
        (external / "config.txt").write_text("external")
        shutil.rmtree(str(self.root / "nested"))
        (self.root / "nested").symlink_to(external, target_is_directory=True)
        manager.rewind_to(checkpoint.id)
        self.assertEqual((external / "config.txt").read_text(), "external")
        self.assertFalse((self.root / "nested").is_symlink())

    def test_rewind_restores_empty_git_checkpoint(self):
        self.init_git()
        (self.root / "base.txt").unlink()
        (self.root / "keep.txt").unlink()
        manager, checkpoint = self.checkpoint()
        (self.root / "base.txt").write_text("later")
        manager.rewind_to(checkpoint.id)
        self.assertFalse((self.root / "base.txt").exists())

    def test_index_backup_survives_split_index_cleanup(self):
        self.init_git()
        (self.root / "base.txt").write_text("staged")
        self.git("add", "base.txt")
        self.git("update-index", "--split-index")
        manager, checkpoint = self.checkpoint()
        self.git("update-index", "--no-split-index")
        for shared in (self.root / ".git").glob("sharedindex.*"):
            shared.unlink()
        manager.rewind_to(checkpoint.id)
        self.assertEqual(self.git("show", ":base.txt"), b"staged")

    def test_rewind_refuses_missing_index_backup_before_modifying_files(self):
        self.init_git()
        manager, checkpoint = self.checkpoint()
        (manager.snapshots_dir / checkpoint.id / "git-index").unlink()
        (self.root / "base.txt").write_text("latest")
        with self.assertRaisesRegex(ValueError, "index|Index"):
            manager.rewind_to(checkpoint.id)
        self.assertEqual((self.root / "base.txt").read_text(), "latest")
        self.assertEqual(self.git("show", ":base.txt"), b"base\n")

    def test_rewind_does_not_delete_ignored_files_obstructing_restoration(self):
        self.init_git()
        (self.root / ".gitignore").write_text("base.txt/\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore obstruction")
        manager, checkpoint = self.checkpoint()
        (self.root / "base.txt").unlink()
        (self.root / "base.txt").mkdir()
        secret = self.root / "base.txt" / "private.txt"
        secret.write_text("private")
        with self.assertRaises((ValueError, OSError)):
            manager.rewind_to(checkpoint.id)
        self.assertEqual(secret.read_text(), "private")

    def test_index_conflict_stages_survive_checkpoint_and_rewind(self):
        self.init_git()
        self.git("checkout", "-qb", "feature")
        (self.root / "base.txt").write_text("feature")
        self.git("commit", "-qam", "feature")
        self.git("checkout", "-q", "-")
        (self.root / "base.txt").write_text("main")
        self.git("commit", "-qam", "main")
        subprocess.run(["git", "merge", "feature"], cwd=str(self.root), capture_output=True)
        stages = self.git("ls-files", "--unmerged", "-z")
        manager, checkpoint = self.checkpoint()
        (self.root / "base.txt").write_text("resolved")
        self.git("add", "base.txt")
        manager.rewind_to(checkpoint.id)
        self.assertEqual(self.git("ls-files", "--unmerged", "-z"), stages)

    def test_keep_untracked_preserves_new_untracked_only(self):
        self.init_git()
        manager, checkpoint = self.checkpoint()
        (self.root / "untracked.txt").write_text("untracked")
        (self.root / "staged.txt").write_text("staged")
        self.git("add", "staged.txt")
        manager.rewind_to(checkpoint.id, keep_untracked=True)
        self.assertEqual((self.root / "untracked.txt").read_text(), "untracked")
        self.assertFalse((self.root / "staged.txt").exists())

    def _remove_and_ignore(self, path):
        self.git("rm", "-r", "--", path)
        (self.root / ".gitignore").write_text(path + "\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "remove and ignore path")

    def test_rewind_refuses_ignored_file_not_saved_in_safety(self):
        self.init_git()
        manager, checkpoint = self.checkpoint()
        self._remove_and_ignore("base.txt")
        (self.root / "base.txt").write_text("new private data")
        (self.root / "keep.txt").write_text("current working data")
        index = self.git("ls-files", "--stage", "-z")
        with self.assertRaisesRegex(ValueError, "not captured|ignored"):
            manager.rewind_to(checkpoint.id)
        self.assertEqual((self.root / "base.txt").read_text(), "new private data")
        self.assertEqual((self.root / "keep.txt").read_text(), "current working data")
        self.assertEqual(self.git("ls-files", "--stage", "-z"), index)

    def test_rewind_refuses_ignored_symlink_not_saved_in_safety(self):
        self.init_git()
        manager, checkpoint = self.checkpoint()
        self._remove_and_ignore("base.txt")
        external = Path(self.temp.name) / "external.txt"
        external.write_text("private external data")
        (self.root / "base.txt").symlink_to(external)
        with self.assertRaisesRegex(ValueError, "not captured|ignored"):
            manager.rewind_to(checkpoint.id)
        self.assertEqual(os.readlink(str(self.root / "base.txt")), str(external))
        self.assertEqual(external.read_text(), "private external data")

    def test_rewind_refuses_file_inside_ignored_directory(self):
        self.init_git()
        (self.root / "nested").mkdir()
        (self.root / "nested" / "private.txt").write_text("old tracked")
        self.git("add", "nested")
        self.git("commit", "-qm", "nested file")
        manager, checkpoint = self.checkpoint()
        self._remove_and_ignore("nested")
        (self.root / "nested").mkdir()
        (self.root / "nested" / "private.txt").write_text("new private data")
        with self.assertRaisesRegex(ValueError, "not captured|ignored"):
            manager.rewind_to(checkpoint.id)
        self.assertEqual((self.root / "nested" / "private.txt").read_text(), "new private data")

    def test_rewind_refuses_ignored_ancestor_symlink(self):
        self.init_git()
        (self.root / "nested").mkdir()
        (self.root / "nested" / "private.txt").write_text("old tracked")
        self.git("add", "nested")
        self.git("commit", "-qm", "nested file")
        manager, checkpoint = self.checkpoint()
        self._remove_and_ignore("nested")
        external = Path(self.temp.name) / "external"
        external.mkdir()
        (external / "private.txt").write_text("private external data")
        (self.root / "nested").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "not captured|ignored"):
            manager.rewind_to(checkpoint.id)
        self.assertEqual(os.readlink(str(self.root / "nested")), str(external))
        self.assertEqual((external / "private.txt").read_text(), "private external data")

    def test_rewind_refuses_deleting_ignored_staged_file_not_saved_in_safety(self):
        self.init_git()
        (self.root / ".gitignore").write_text("private.txt\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore private file")
        manager, checkpoint = self.checkpoint()
        (self.root / "private.txt").write_text("staged private data")
        self.git("add", "-f", "private.txt")
        (self.root / "private.txt").write_text("new working private data")
        with self.assertRaisesRegex(ValueError, "not captured|ignored"):
            manager.rewind_to(checkpoint.id)
        self.assertEqual((self.root / "private.txt").read_text(), "new working private data")
        self.assertEqual(self.git("show", ":private.txt"), b"staged private data")

