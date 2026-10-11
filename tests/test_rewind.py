"""Unit and integration tests for Vibe Rewind (AI Session Checkpoint and Time Machine)."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from vibeguard.cli import main
from vibeguard.mcp import MCPServer
from vibeguard.rewind import CheckpointManager, CheckpointMetadata
from vibeguard.server import VibeGuardRequestHandler
from vibeguard.ui import VibeGuardService


class TestVibeRewind(unittest.TestCase):
    """Test suite for checkpointing and time-machine rewinding."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = Path(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _init_git_repo(self):
        subprocess.run(["git", "init"], cwd=str(self.root), check=True, stdout=subprocess.PIPE)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(self.root), check=True)
        subprocess.run(["git", "config", "user.email", "test@vibeguard.dev"], cwd=str(self.root), check=True)

        f1 = self.root / "module.py"
        f1.write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
        subprocess.run(["git", "add", "module.py"], cwd=str(self.root), check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(self.root), check=True, stdout=subprocess.PIPE)

    def test_checkpoint_metadata_serialization(self):
        meta = CheckpointMetadata(
            id="cp_test_123",
            timestamp=1700000000.0,
            created_at="2026-10-10T21:00:00",
            message="Test checkpoint",
            is_safety=False,
            git_commit="abc1234",
            git_ref="refs/vibeguard/checkpoints/cp_test_123",
            files_count=2,
            changed_files=["module.py"],
            untracked_files=["new.py"],
            drift_status="clean",
        )
        d = meta.to_dict()
        self.assertEqual(d["id"], "cp_test_123")
        self.assertEqual(d["drift_status"], "clean")

        restored = CheckpointMetadata.from_dict(d)
        self.assertEqual(restored.id, meta.id)
        self.assertEqual(restored.message, meta.message)
        self.assertEqual(restored.changed_files, ["module.py"])

    def test_create_checkpoint_git_repo(self):
        self._init_git_repo()
        mgr = CheckpointManager(str(self.root))

        # Modify module.py and add an untracked file
        (self.root / "module.py").write_text("def hello():\n    return 'v2'\n", encoding="utf-8")
        (self.root / "helper.py").write_text("def helper(): pass\n", encoding="utf-8")

        cp = mgr.create_checkpoint(message="Before risky AI prompt")
        self.assertTrue(cp.id.startswith("cp_"))
        self.assertFalse(cp.is_safety)
        self.assertEqual(cp.message, "Before risky AI prompt")
        self.assertIsNotNone(cp.git_commit)
        self.assertIsNotNone(cp.git_ref)

        # Check that index was saved
        cps = mgr.list_checkpoints()
        self.assertEqual(len(cps), 1)
        self.assertEqual(cps[0].id, cp.id)

    def test_rewind_step_and_safety_backup(self):
        self._init_git_repo()
        mgr = CheckpointManager(str(self.root))

        # Checkpoint 1 (Good state)
        (self.root / "module.py").write_text("def hello():\n    return 'good'\n", encoding="utf-8")
        cp1 = mgr.create_checkpoint(message="Checkpoint 1: Working code")

        # Now AI makes a disastrous edit and adds garbage files
        (self.root / "module.py").write_text("broken syntax def ()\n", encoding="utf-8")
        (self.root / "hallucinated.py").write_text("# AI garbage\n", encoding="utf-8")
        self.assertTrue((self.root / "hallucinated.py").exists())

        # Rewind 1 step
        res = mgr.rewind_step(step=1)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["target_checkpoint"]["id"], cp1.id)
        self.assertTrue(res["safety_checkpoint"]["is_safety"])

        # Verify module.py is restored to 'good'
        content = (self.root / "module.py").read_text(encoding="utf-8")
        self.assertEqual(content, "def hello():\n    return 'good'\n")

        # Verify hallucinated.py was cleanly removed
        self.assertFalse((self.root / "hallucinated.py").exists())

        # Verify a safety checkpoint was automatically created before rewinding
        safety_id = res["safety_checkpoint"]["id"]
        self.assertTrue(safety_id.startswith("safety_"))

        # Test undoing rewind via the safety checkpoint!
        undo_res = mgr.rewind_to(safety_id)
        self.assertEqual(undo_res["status"], "success")
        self.assertTrue((self.root / "hallucinated.py").exists())
        self.assertEqual((self.root / "module.py").read_text(encoding="utf-8"), "broken syntax def ()\n")

    def test_rewind_to_specific_id(self):
        self._init_git_repo()
        mgr = CheckpointManager(str(self.root))

        (self.root / "module.py").write_text("v1\n", encoding="utf-8")
        cp1 = mgr.create_checkpoint(message="v1")

        (self.root / "module.py").write_text("v2\n", encoding="utf-8")
        cp2 = mgr.create_checkpoint(message="v2")

        (self.root / "module.py").write_text("v3\n", encoding="utf-8")

        # Rewind explicitly to cp1
        res = mgr.rewind_to(cp1.id)
        self.assertEqual(res["target_checkpoint"]["id"], cp1.id)
        self.assertEqual((self.root / "module.py").read_text(encoding="utf-8"), "v1\n")

    def test_non_git_checkpoint_and_rewind(self):
        # Non-git directory
        mgr = CheckpointManager(str(self.root))
        self.assertFalse(mgr.is_git_repo())

        (self.root / "main.py").write_text("print('stable')\n", encoding="utf-8")
        cp = mgr.create_checkpoint(message="non-git checkpoint")

        # AI breaks main.py and creates junk.py
        (self.root / "main.py").write_text("print('broken')\n", encoding="utf-8")
        (self.root / "junk.py").write_text("unwanted\n", encoding="utf-8")

        res = mgr.rewind_to(cp.id)
        self.assertEqual(res["status"], "success")
        self.assertEqual((self.root / "main.py").read_text(encoding="utf-8"), "print('stable')\n")
        self.assertFalse((self.root / "junk.py").exists())

    def test_get_diff(self):
        self._init_git_repo()
        mgr = CheckpointManager(str(self.root))

        (self.root / "module.py").write_text("original\n", encoding="utf-8")
        cp = mgr.create_checkpoint(message="orig")

        (self.root / "module.py").write_text("modified\n", encoding="utf-8")
        diff = mgr.get_diff(cp.id)
        self.assertIn("original", diff)
        self.assertIn("modified", diff)

    def test_delete_checkpoint(self):
        self._init_git_repo()
        mgr = CheckpointManager(str(self.root))
        cp = mgr.create_checkpoint(message="to delete")
        self.assertEqual(len(mgr.list_checkpoints()), 1)

        deleted = mgr.delete_checkpoint(cp.id)
        self.assertTrue(deleted)
        self.assertEqual(len(mgr.list_checkpoints()), 0)

    def test_cli_checkpoint_and_rewind(self):
        self._init_git_repo()
        old_cwd = os.getcwd()
        os.chdir(str(self.root))
        try:
            # Create checkpoint via CLI
            code = main(["checkpoint", "Testing", "CLI", "checkpoint"])
            self.assertEqual(code, 0)

            # List checkpoints via CLI
            code_list = main(["rewind", "--list"])
            self.assertEqual(code_list, 0)

            # Modify and rewind
            (self.root / "module.py").write_text("changed\n", encoding="utf-8")
            code_rw = main(["rewind", "--step", "1"])
            self.assertEqual(code_rw, 0)

            # Alias 'checkpoints'
            code_alias = main(["checkpoints"])
            self.assertEqual(code_alias, 0)
        finally:
            os.chdir(old_cwd)

    def test_mcp_checkpoint_and_rewind(self):
        self._init_git_repo()
        server = MCPServer(project_path=str(self.root))

        # Checkpoint tool
        cp_res = server.call_tool("vibeguard_checkpoint", {"message": "MCP Checkpoint Test"})
        self.assertIn("checkpoint", cp_res)
        cp_id = cp_res["checkpoint"]["id"]

        # Modify file
        (self.root / "module.py").write_text("changed by ai\n", encoding="utf-8")

        # Rewind tool
        rw_res = server.call_tool("vibeguard_rewind", {"checkpoint_id": cp_id})
        self.assertEqual(rw_res["status"], "success")
        self.assertEqual((self.root / "module.py").read_text(encoding="utf-8"), "def hello():\n    return 'hello'\n")

    def test_ui_api_checkpoints_and_rewind(self):
        self._init_git_repo()
        service = VibeGuardService(str(self.root))

        # Create checkpoint via service
        cp = service.create_checkpoint(message="UI Checkpoint Test")
        self.assertTrue(cp["id"].startswith("cp_"))

        # List checkpoints via service
        cps = service.get_checkpoints()
        self.assertEqual(len(cps), 1)

        # Modify and rewind
        (self.root / "module.py").write_text("changed\n", encoding="utf-8")
        rw = service.rewind(checkpoint_id=cp["id"])
        self.assertEqual(rw["status"], "success")
        self.assertEqual((self.root / "module.py").read_text(encoding="utf-8"), "def hello():\n    return 'hello'\n")


if __name__ == "__main__":
    unittest.main()
