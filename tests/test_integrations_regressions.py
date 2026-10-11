"""Regression coverage for Action execution, badges and actionable patches."""

import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from vibeguard.badge import COLOR_VERIFIED, get_project_state, get_state_from_report
from vibeguard.drift import DriftEngine, DriftFinding, DriftReport
from vibeguard.remediation import RemediationGenerator


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestRemediationRegressions(unittest.TestCase):
    def _patch_for_removal(self, root, filename, original, current):
        target = root / filename
        target.write_text(original, encoding="utf-8")
        engine = DriftEngine(str(root))
        baseline = engine.snapshot_engine.create_snapshot()
        target.write_text(current, encoding="utf-8")
        report = engine.evaluate(baseline=baseline)
        patch_text = RemediationGenerator.generate_git_patch(report, str(root), baseline)
        self.assertTrue(patch_text, "The missing contract should have actionable guidance")
        patch_file = root / "fix.patch"
        patch_file.write_text(patch_text, encoding="utf-8")
        checked = subprocess.run(["git", "apply", "--check", str(patch_file)], cwd=root,
                                 text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        applied = subprocess.run(["git", "apply", str(patch_file)], cwd=root,
                                 text=True, capture_output=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        return target.read_text(encoding="utf-8"), engine, baseline

    def test_python_method_guidance_keeps_valid_syntax_and_does_not_invent_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, engine, baseline = self._patch_for_removal(
                Path(tmp), "app.py",
                "class Service:\n    def run(self, value):\n        return value\n    def keep(self):\n        return 1\n",
                "class Service:\n    def keep(self):\n        return 1\n")
            ast.parse(result)
            self.assertTrue(engine.evaluate(baseline=baseline).has_blocking_errors)

    def test_python_class_is_not_replaced_with_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, engine, baseline = self._patch_for_removal(
                Path(tmp), "app.py", "class Service:\n    def run(self):\n        return 1\n", "value = 1\n")
            tree = ast.parse(result)
            self.assertFalse(any(isinstance(node, ast.FunctionDef) and node.name == "Service"
                                 for node in tree.body))
            self.assertTrue(engine.evaluate(baseline=baseline).has_blocking_errors)

    def test_go_and_rust_guidance_does_not_generate_untyped_functions(self):
        fixtures = [
            ("main.go", "package main\nfunc StartServer(port int) { return }\n", "package main\n"),
            ("lib.rs", "pub fn compute_hash(data: &[u8]) { let _ = data; }\n", "// empty\n"),
        ]
        for filename, original, current in fixtures:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                result, engine, baseline = self._patch_for_removal(Path(tmp), filename, original, current)
                executable_lines = [line for line in result.splitlines() if not line.strip().startswith("//")]
                self.assertNotIn("func StartServer(port)", "\n".join(executable_lines))
                self.assertNotIn("pub fn compute_hash(data)", "\n".join(executable_lines))
                self.assertTrue(engine.evaluate(baseline=baseline).has_blocking_errors)

    def test_javascript_method_guidance_does_not_emit_invalid_dotted_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, engine, baseline = self._patch_for_removal(
                Path(tmp), "app.js", "export class Service {\n  run(value) { return value; }\n  keep() { return 1; }\n}\n",
                "export class Service {\n  keep() { return 1; }\n}\n")
            self.assertNotIn("export function Service.run", result)
            self.assertTrue(engine.evaluate(baseline=baseline).has_blocking_errors)

    def test_patch_applies_to_file_without_final_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _ = self._patch_for_removal(
                Path(tmp), "app.py", "def run():\n    return 1\n", "value = 1")
            ast.parse(result)
            self.assertIn("value = 1", result)


class TestBadgeFailureRegressions(unittest.TestCase):
    def test_missing_report_with_baseline_never_means_verified(self):
        state = get_state_from_report(None, True)
        self.assertNotEqual(state.status, "verified")
        self.assertNotEqual(state.color, COLOR_VERIFIED)

    def test_audit_exception_is_visible_in_badge_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            DriftEngine(str(root)).snapshot_engine.save_baseline()
            with patch("vibeguard.badge.DriftEngine.evaluate", side_effect=OSError("Cannot read source")):
                state = get_project_state(root)
            self.assertEqual(state.status, "error")
            self.assertIn("Cannot read source", state.to_dict().get("error", ""))
            self.assertNotEqual(state.color, COLOR_VERIFIED)


class TestAnnotationRegressions(unittest.TestCase):
    def test_annotations_encode_control_characters_and_property_separators(self):
        finding = DriftFinding("api_breakage", "error", "app%,:x.py\r\n::notice::spoof", 1,
                               "Missing 100%", "first\n::warning::spoof\r", "restore")
        report = DriftReport(1, 0, 1, 0, [finding], True)
        output = RemediationGenerator.export_github_annotations(report)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("file=app%25%2C%3Ax.py%0D%0A%3A%3Anotice%3A%3Aspoof", output)
        self.assertIn("Missing 100%25: first%0A::warning::spoof%0D", output)


class TestActionExecutionRegressions(unittest.TestCase):
    def _run_action(self, input_baseline):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            action = base / "trusted action"
            workspace = base / "workspace"
            for directory in (action / "bin", action / "vibeguard", workspace / "vibeguard"):
                directory.mkdir(parents=True)
            (action / "bin/vibeguard").write_text((PROJECT_ROOT / "bin/vibeguard").read_text(), encoding="utf-8")
            (action / "vibeguard/__init__.py").write_text("", encoding="utf-8")
            (action / "vibeguard/cli.py").write_text(
                "import sys, json\ndef main():\n    print(json.dumps(sys.argv[1:]))\n    return 0\n", encoding="utf-8")
            (workspace / "vibeguard/__init__.py").write_text("", encoding="utf-8")
            (workspace / "vibeguard/cli.py").write_text(
                "from pathlib import Path\nPath('shadow-executed').touch()\n", encoding="utf-8")
            script = (PROJECT_ROOT / "action.yml").read_text().split("      run: |\n", 1)[1]
            script = textwrap.dedent(script.split("\n    - name: Upload", 1)[0])
            values = {"fail-on-drift": "true", "comment-pr": "false", "create-patch": "false",
                      "baseline-file": input_baseline, "github-token": "benign"}
            for key, value in values.items():
                script = script.replace("${{ inputs." + key + " }}", value)
            env = os.environ.copy()
            env.update({"VIBEGUARD_ACTION_PATH": str(action), "GITHUB_WORKSPACE": str(workspace),
                        "PYTHONPATH": str(action) + ":" + str(workspace)})
            env.update({"INPUT_" + key.upper().replace("-", "_"): value for key, value in values.items()})
            run = subprocess.run(["bash", "-e", "-c", script], cwd=workspace, env=env, text=True, capture_output=True)
            return run, (workspace / "injected-executed").exists(), (workspace / "shadow-executed").exists(), workspace

    def test_action_passes_input_literally_without_shell_execution(self):
        run, injected, _, _ = self._run_action("$(touch injected-executed)")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(injected)
        self.assertIn("$(touch injected-executed)", json.loads(run.stdout))

    def test_action_executes_its_package_and_targets_workspace_explicitly(self):
        run, _, shadow, workspace = self._run_action("baseline.json")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(shadow)
        arguments = json.loads(run.stdout)
        self.assertEqual(arguments[arguments.index("--project") + 1], str(workspace))


if __name__ == "__main__":
    unittest.main()
