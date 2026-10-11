"""Unit and integration tests for FileWatcher and real-time monitoring."""

import tempfile
import time
import unittest
from pathlib import Path

from vibeguard.config import GuardConfig
from vibeguard.snapshot import SnapshotEngine
from vibeguard.watcher import FileWatcher


class TestFileWatcher(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temp_dir.name)
        self.config = GuardConfig(
            project_name="watch_test",
            protected_paths=["*.py", "*.java"],
            max_file_lines=50,
            fail_on_hallucinated_imports=True,
        )
        self.config.save_to_file(str(self.project_path / ".vibeguard.json"))

        # Base file
        self.f1 = self.project_path / "app.py"
        self.f1.write_text("def run():\n    return True\n", encoding="utf-8")

        # Save baseline
        engine = SnapshotEngine(str(self.project_path), self.config)
        engine.save_baseline()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_watcher_detects_clean_state_initially(self):
        watcher = FileWatcher(str(self.project_path), self.config, poll_interval=0.1)
        report = watcher.watch_once()
        self.assertIsNotNone(report)
        self.assertEqual(report.total_findings, 0)

    def test_watcher_detects_file_modification_drift(self):
        watcher = FileWatcher(str(self.project_path), self.config, poll_interval=0.1)
        # First pass establishes baseline state
        watcher.watch_once()

        # Modify file with hallucinated import and stub
        time.sleep(0.05)
        self.f1.write_text("import hallucinated_pkg\n\ndef run():\n    pass\n", encoding="utf-8")

        # Second pass detects change and evaluates drift
        changed = watcher.check_changes()
        self.assertIn("app.py", changed)

        report = watcher.drift_engine.evaluate(target_files=list(changed))
        self.assertGreater(report.total_findings, 0)
        categories = [f.category for f in report.findings]
        self.assertIn("hallucinated_import", categories)

    def test_watcher_detects_newly_added_file(self):
        watcher = FileWatcher(str(self.project_path), self.config, poll_interval=0.1)
        watcher.watch_once()

        time.sleep(0.05)
        new_file = self.project_path / "Service.java"
        new_file.write_text("public class Service {}\n", encoding="utf-8")

        changed = watcher.check_changes()
        self.assertIn("Service.java", changed)

    def test_watcher_run_loop_with_max_iterations(self):
        watcher = FileWatcher(str(self.project_path), self.config, poll_interval=0.05, debounce_seconds=0.01)
        reports_received = []

        def callback(rep, changed):
            reports_received.append((rep, changed))

        watcher.run_loop(on_report=callback, max_iterations=2)
        # Should have run initial pass plus 2 iterations
        self.assertGreaterEqual(len(reports_received), 1)


if __name__ == "__main__":
    unittest.main()
