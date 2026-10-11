"""Regression tests for the audit's cross-interface and state integrity bugs."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from vibeguard.cli import main
from vibeguard.mcp import MCPServer
from vibeguard.server import create_server
from vibeguard.snapshot import SnapshotEngine
from vibeguard.ui import VibeGuardService
from vibeguard.watcher import FileWatcher


class TestAuditRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "app.py"
        self.clean = "def hello():\n    return 1\n"
        self.source.write_text(self.clean)
        self.git("init")
        self.git("config", "user.email", "audit@example.invalid")
        self.git("config", "user.name", "Audit")
        self.git("add", "app.py")
        self.git("commit", "-m", "initial")
        SnapshotEngine(str(self.root)).save_baseline()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(args))
        return code, output.getvalue()

    def test_staged_uses_index_instead_of_clean_working_copy(self):
        self.source.write_text("import nonexistent_audit_pkg\n" + self.clean)
        self.git("add", "app.py")
        self.source.write_text(self.clean)
        code, output = self.cli("check", "--project", str(self.root), "--staged", "--json")
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(output)["has_blocking_errors"])

    def test_staged_does_not_block_for_unstaged_error(self):
        self.source.write_text(self.clean + "\n")
        self.git("add", "app.py")
        self.source.write_text("import nonexistent_audit_pkg\n" + self.clean)
        code, _ = self.cli("check", "--project", str(self.root), "--staged")
        self.assertEqual(code, 0)

    def test_staged_deleted_file_is_not_restored_by_unstaged_copy(self):
        self.git("rm", "app.py")
        self.source.write_text(self.clean)
        code, _ = self.cli("check", "--project", str(self.root), "--staged")
        self.assertEqual(code, 1)

    def test_staged_dependencies_come_from_index(self):
        self.source.write_text("import dependency_only_on_disk_audit\n" + self.clean)
        self.git("add", "app.py")
        (self.root / "requirements.txt").write_text("dependency_only_on_disk_audit>=1\n")
        code, _ = self.cli("check", "--project", str(self.root), "--staged")
        self.assertEqual(code, 1)

    def test_unstaged_config_cannot_disable_index_checks(self):
        self.source.write_text("import nonexistent_audit_pkg\n" + self.clean)
        self.git("add", "app.py")
        (self.root / ".vibeguard.json").write_text(json.dumps({"fail_on_hallucinated_imports": False}))
        code, _ = self.cli("check", "--project", str(self.root), "--staged")
        self.assertEqual(code, 1)

    def test_invalid_unstaged_config_does_not_override_valid_index_config(self):
        config = self.root / ".vibeguard.json"
        config.write_text("{}")
        self.git("add", ".vibeguard.json")
        config.write_text("{broken")
        code, output = self.cli("check", "--project", str(self.root), "--staged", "--json")
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)["has_blocking_errors"])

    def test_subproject_staged_paths_are_relative_to_selected_project(self):
        sub = self.root / "service"
        sub.mkdir()
        source = sub / "api.py"
        source.write_text(self.clean)
        self.git("add", "service/api.py")
        self.git("commit", "-m", "subproject")
        service = VibeGuardService(str(sub))
        service.update_baseline()
        source.write_text("import nonexistent_audit_pkg\n" + self.clean)
        self.git("add", "service/api.py")
        self.assertTrue(service.run_check(staged=True)["has_blocking_errors"])
        result = MCPServer(str(sub)).call_tool("vibeguard_check", {"staged": True})
        self.assertTrue(result["has_blocking_errors"])

    def test_json_output_is_machine_readable_for_clean_and_empty_scopes(self):
        for flags in ([], ["--staged"], ["--diff"]):
            code, output = self.cli("check", "--project", str(self.root), "--json", *flags)
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(output)["has_blocking_errors"])

    def test_cli_does_not_need_boolean_optional_action(self):
        with patch.object(argparse, "BooleanOptionalAction", create=True, new=None):
            code, _ = self.cli("check", "--project", str(self.root))
        self.assertEqual(code, 0)

    def test_invalid_existing_baseline_cannot_disable_contract_checks(self):
        (self.root / ".vibeguard/baseline.json").write_text("{truncated")
        code, output = self.cli("check", "--project", str(self.root), "--json")
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(output)["has_blocking_errors"])

    def test_parser_failure_cannot_approve_audit_or_badge(self):
        service = VibeGuardService(str(self.root))
        with patch("vibeguard.snapshot.CodeAnalyzer.analyze_file", side_effect=RuntimeError("parser failed")):
            for has_baseline in (True, False):
                with self.subTest(has_baseline=has_baseline):
                    if not has_baseline:
                        (self.root / ".vibeguard/baseline.json").unlink()
                    code, output = self.cli("check", "--project", str(self.root), "--json")
                    self.assertEqual(code, 1)
                    self.assertTrue(json.loads(output)["has_blocking_errors"])
                    self.assertEqual(service.get_badge()["status"], "error")

    def test_unreadable_protected_file_cannot_approve_audit(self):
        (self.root / ".vibeguard/baseline.json").unlink()
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == self.source:
                raise PermissionError("file unreadable")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            code, output = self.cli("check", "--project", str(self.root), "--json")
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(output)["has_blocking_errors"])

    def test_invalid_symbol_metadata_fails_with_structured_error(self):
        baseline = self.root / ".vibeguard/baseline.json"
        data = json.loads(baseline.read_text())
        data["files"]["app.py"]["symbols"] = ["broken"]
        baseline.write_text(json.dumps(data))
        code, output = self.cli("check", "--project", str(self.root), "--json")
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(output)["has_blocking_errors"])

    def test_dashboard_dependencies_are_current_even_with_baseline(self):
        service = VibeGuardService(str(self.root))
        self.source.write_text("import nonexistent_audit_pkg\n" + self.clean)
        self.assertEqual(service.get_dependencies()["hallucinated_count"], 1)

    def test_dashboard_prompt_and_badge_refresh_after_edit(self):
        service = VibeGuardService(str(self.root))
        service.run_check()
        self.source.write_text("import nonexistent_audit_pkg\n" + self.clean)
        self.assertTrue(service.get_prompt()["has_blocking_errors"])
        self.assertNotEqual(service.get_badge()["status"], "verified")

    def test_invalid_config_does_not_replace_valid_configuration(self):
        service = VibeGuardService(str(self.root))
        service.update_config({"max_file_lines": 300})
        with self.assertRaises(ValueError):
            service.update_config({"max_file_lines": None})
        self.assertEqual(service.config.max_file_lines, 300)
        self.assertEqual(json.loads((self.root / ".vibeguard.json").read_text())["max_file_lines"], 300)

    def test_mcp_duplicate_policy_matches_check(self):
        result = MCPServer(str(self.root)).call_tool("vibeguard_validate_code", {
            "file_path": "app.py", "content": "def hello():\n return 1\ndef hello():\n return 2\n"})
        self.assertFalse(result["valid"])
        self.assertTrue(any(f["category"] == "duplicate_declaration" for f in result["findings"]))

    def test_mcp_protects_current_contracts_of_files_added_after_baseline(self):
        (self.root / "new.py").write_text("def new_public():\n    return 1\n")
        result = MCPServer(str(self.root)).call_tool("vibeguard_validate_code", {
            "file_path": "new.py", "content": "value = 1\n"})
        self.assertFalse(result["valid"])
        self.assertTrue(any(f["category"] == "api_breakage" for f in result["findings"]))

    def test_mcp_requires_proposed_content(self):
        server = MCPServer(str(self.root))
        result = server.handle_message(json.dumps({"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": "vibeguard_validate_code",
            "arguments": {"file_path": "new.py"}}}))
        self.assertTrue(result["result"]["isError"])

    def test_mcp_recovers_after_malformed_line(self):
        server = MCPServer(str(self.root))
        output = io.StringIO()
        server.run_loop(io.StringIO('{"broken"}\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n'), output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[1]["id"], 9)

    def test_mcp_recovers_after_truncated_line(self):
        output = io.StringIO()
        MCPServer(str(self.root)).run_loop(io.StringIO(
            '{"broken"\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n'), output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["id"], 9)

    def test_watcher_includes_changes_during_debounce(self):
        other = self.root / "other.py"
        other.write_text("def other():\n return 1\n")
        SnapshotEngine(str(self.root)).save_baseline()
        watcher = FileWatcher(str(self.root))
        calls = []
        ticks = [0]

        def sleep(_):
            ticks[0] += 1
            if ticks[0] == 1:
                self.source.write_text(self.clean + "\n")
            elif ticks[0] == 2:
                other.write_text("def other():\n pass\n")

        with patch("vibeguard.watcher.time.sleep", side_effect=sleep):
            watcher.run_loop(lambda report, changed: calls.append((report, changed)), max_iterations=2)
        self.assertIn("other.py", calls[1][1])
        self.assertTrue(calls[1][0].has_blocking_errors)

    def test_baseline_operation_keeps_its_project_during_switch(self):
        other = self.root / "other"
        other.mkdir()
        (other / "different.py").write_text(self.clean)
        service = VibeGuardService(str(self.root))
        captured, release, finished, switched = threading.Event(), threading.Event(), threading.Event(), threading.Event()
        original = service.snapshot_engine.create_snapshot
        errors = []

        def snapshot():
            result = original()
            captured.set()
            release.wait(3)
            return result

        def update():
            try:
                service.update_baseline()
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        service.snapshot_engine.create_snapshot = snapshot
        thread = threading.Thread(target=update)
        thread.start()
        self.assertTrue(captured.wait(3))
        def select():
            service.select_project(str(other))
            switched.set()

        switch = threading.Thread(target=select)
        switch.start()
        switched.wait(0.2)
        release.set()
        thread.join(3)
        switch.join(3)
        self.assertFalse(errors)
        self.assertTrue(finished.is_set())
        self.assertFalse((other / ".vibeguard/baseline.json").exists())


class TestDashboardAccess(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = create_server(port=0, service=VibeGuardService(str(self.root)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = "http://127.0.0.1:{}".format(self.server.server_address[1])

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, headers=None):
        req = urllib.request.Request(self.url + "/api/config", data=b'{"max_file_lines":17}',
                                     headers={"Content-Type": "application/json", **(headers or {})})
        return urllib.request.urlopen(req)

    def test_mutation_requires_session_token(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request()
        error.exception.close()
        self.assertEqual(error.exception.code, 403)
        self.assertFalse((self.root / ".vibeguard.json").exists())

    def test_foreign_origin_cannot_mutate_with_token(self):
        token = getattr(self.server, "api_token", "missing")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request({"Origin": "https://untrusted.example", "X-VibeGuard-Token": token})
        error.exception.close()
        self.assertEqual(error.exception.code, 403)

    def test_authenticated_local_client_can_mutate(self):
        token = getattr(self.server, "api_token", "missing")
        with self.request({"X-VibeGuard-Token": token}) as response:
            self.assertEqual(response.status, 200)
            self.assertNotEqual(response.headers.get("Access-Control-Allow-Origin"), "*")


if __name__ == "__main__":
    unittest.main()
