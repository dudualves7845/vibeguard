"""End-to-end and regression tests for DriftEngine and Remediation."""

import json
import tempfile
import unittest
from pathlib import Path
from vibeguard.config import GuardConfig, IntentRule
from vibeguard.drift import DriftEngine
from vibeguard.remediation import RemediationGenerator
from vibeguard.snapshot import SnapshotEngine


class TestDriftEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temp_dir.name)

        # Create basic configuration
        self.config = GuardConfig(
            project_name="test_proj",
            protected_paths=["*.py"],
            max_file_lines=50,
            fail_on_hallucinated_imports=True,
            rules=[
                IntentRule(
                    id="no-mock-in-app",
                    description="No mock in app files",
                    rule_type="forbidden_import",
                    target_patterns=["app.py"],
                    parameters={"forbidden": ["unittest.mock"]},
                    severity="error",
                )
            ],
        )
        self.config.save_to_file(str(self.project_path / ".vibeguard.json"))

        # Create initial baseline code
        self.app_file = self.project_path / "app.py"
        self.app_file.write_text(
            """def calculate_discount(price, rate):
    return price * (1 - rate)

def process_payment(amount):
    return True
""",
            encoding="utf-8",
        )

        # Save baseline
        engine = SnapshotEngine(str(self.project_path), self.config)
        engine.save_baseline()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_clean_baseline_has_no_drift(self):
        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate()
        self.assertEqual(report.total_findings, 0)
        self.assertFalse(report.has_blocking_errors)

    def test_stub_remediation_is_complete(self):
        self.app_file.write_text("def calculate_discount(price, rate):\n    pass\n", encoding="utf-8")
        report = DriftEngine(str(self.project_path), self.config).evaluate()
        stub = next(f for f in report.findings if f.category == "stub_injected")
        self.assertIn("Complete implementation and test before shipping to production.", stub.remediation)
        self.assertNotIn("[...]", stub.remediation)

    def test_detects_removed_public_function(self):
        # AI vibe coding removed process_payment!
        self.app_file.write_text(
            """def calculate_discount(price, rate):
    return price * (1 - rate)
""",
            encoding="utf-8",
        )

        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate()
        self.assertGreaterEqual(report.error_count, 1)

        categories = [f.category for f in report.findings]
        self.assertIn("api_breakage", categories)

    def test_detects_hallucinated_import_and_stubs(self):
        # AI added fake import and empty pass function
        self.app_file.write_text(
            """import hallucinated_fake_crypto_lib

def calculate_discount(price, rate):
    return price * (1 - rate)

def process_payment(amount):
    pass
""",
            encoding="utf-8",
        )

        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate()

        categories = [f.category for f in report.findings]
        self.assertIn("hallucinated_import", categories)
        self.assertIn("stub_injected", categories)

    def test_detects_rule_violation(self):
        # AI imported forbidden module
        self.app_file.write_text(
            """import unittest.mock

def calculate_discount(price, rate):
    return price * (1 - rate)

def process_payment(amount):
    return True
""",
            encoding="utf-8",
        )

        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate()

        categories = [f.category for f in report.findings]
        self.assertIn("rule_violation", categories)

    def test_remediation_prompt_generation(self):
        # Break something
        self.app_file.write_text(
            """def calculate_discount(price):
    pass
""",
            encoding="utf-8",
        )
        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate()
        prompt = RemediationGenerator.generate_ai_prompt(report)

        self.assertIn("VIBEGUARD ARCHITECTURAL DRIFT DETECTED", prompt)
        self.assertIn("calculate_discount", prompt)

    def test_detects_method_removal_with_same_name_in_different_classes(self):
        # Multiple classes with execute() method
        self.app_file.write_text(
            """class UserService:
    def execute(self):
        return 'user'

class OrderService:
    def execute(self):
        return 'order'
""",
            encoding="utf-8",
        )
        engine = SnapshotEngine(str(self.project_path), self.config)
        engine.save_baseline()

        # AI silently deletes execute from UserService
        self.app_file.write_text(
            """class UserService:
    def other(self):
        return 1

class OrderService:
    def execute(self):
        return 'order'
""",
            encoding="utf-8",
        )
        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate()
        titles = [f.title for f in report.findings]
        self.assertTrue(any("UserService.execute" in t for t in titles))

    def test_detects_parameter_wipeout_signature_drift(self):
        # Baseline has function with arguments
        self.app_file.write_text(
            """def add(a, b):
    return a + b
""",
            encoding="utf-8",
        )
        engine = SnapshotEngine(str(self.project_path), self.config)
        engine.save_baseline()

        # AI replaces function with zero-arg version
        self.app_file.write_text(
            """def add():
    return 0
""",
            encoding="utf-8",
        )
        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate()
        self.assertTrue(any(f.category == "api_breakage" and "Parameter signature changed" in f.title for f in report.findings))

    def test_target_files_filter_evaluates_only_specified_files(self):
        # Create second file
        other_file = self.project_path / "other.py"
        other_file.write_text("import fake_module_for_other\n", encoding="utf-8")

        drift_engine = DriftEngine(str(self.project_path), self.config)
        # Evaluate only app.py - other.py should not be in findings
        report = drift_engine.evaluate(target_files=["app.py"])
        files_in_findings = {f.file_path for f in report.findings}
        self.assertNotIn("other.py", files_in_findings)

        # Now evaluate other.py
        report_other = drift_engine.evaluate(target_files=["other.py"])
        files_in_findings_other = {f.file_path for f in report_other.findings}
        self.assertIn("other.py", files_in_findings_other)

    def test_sarif_and_github_annotations_export(self):
        # Make a finding
        self.app_file.write_text("import hallucinated_sarif_lib\n", encoding="utf-8")
        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate()

        # SARIF
        sarif = RemediationGenerator.export_sarif(report)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertIn("runs", sarif)
        self.assertEqual(len(sarif["runs"]), 1)
        self.assertGreaterEqual(len(sarif["runs"][0]["results"]), 1)
        all_messages = " ".join(r["message"]["text"] for r in sarif["runs"][0]["results"])
        self.assertIn("hallucinated_sarif_lib", all_messages)

        # Annotations
        annotations = RemediationGenerator.export_github_annotations(report)
        self.assertIn("::error file=app.py", annotations)
        self.assertIn("Hallucinated Python package import", annotations)

    def test_git_patch_generation_for_missing_public_symbol(self):
        # Baseline had calculate_discount and process_payment
        # Simulate AI deleting process_payment
        self.app_file.write_text(
            """def calculate_discount(price, rate):
    return price * (1 - rate)
""",
            encoding="utf-8",
        )
        drift_engine = DriftEngine(str(self.project_path), self.config)
        baseline = drift_engine.snapshot_engine.load_baseline()
        report = drift_engine.evaluate(baseline=baseline)

        patch = RemediationGenerator.generate_git_patch(report, str(self.project_path), baseline)
        self.assertIn("--- a/app.py", patch)
        self.assertIn("+++ b/app.py", patch)
        self.assertIn('restore "function" "process_payment"', patch)
        self.assertNotIn("NotImplementedError", patch)

    def test_git_patch_generation_for_go_and_rust(self):
        poly_config = GuardConfig(
            project_name="poly",
            protected_paths=["*.py", "*.go", "*.rs"],
        )
        # Go patch test
        go_file = self.project_path / "main.go"
        go_file.write_text("package main\n\nfunc StartServer(port int) {\n}\n", encoding="utf-8")
        
        # Rust patch test
        rs_file = self.project_path / "lib.rs"
        rs_file.write_text("pub fn compute_hash(data: &[u8]) {\n}\n", encoding="utf-8")

        engine = SnapshotEngine(str(self.project_path), poly_config)
        snap = engine.create_snapshot()
        engine.save_baseline(snap)

        # AI removes functions in both
        go_file.write_text("package main\n", encoding="utf-8")
        rs_file.write_text("// empty\n", encoding="utf-8")

        drift_engine = DriftEngine(str(self.project_path), poly_config)
        report = drift_engine.evaluate(baseline=snap)
        patch = RemediationGenerator.generate_git_patch(report, str(self.project_path), snap)

        self.assertIn('restore "function" "StartServer"', patch)
        self.assertIn('restore "function" "compute_hash"', patch)
        self.assertNotIn("func StartServer(port)", patch)
        self.assertNotIn("pub fn compute_hash(data)", patch)

    def test_remediation_guidance_keeps_missing_contract_blocking(self):
        import ast
        import subprocess

        self.app_file.write_text(
            "def calculate_discount(price, rate):\n    return price * (1 - rate)\n",
            encoding="utf-8",
        )
        engine = DriftEngine(str(self.project_path), self.config)
        baseline = engine.snapshot_engine.load_baseline()
        report = engine.evaluate(baseline=baseline)
        patch_text = RemediationGenerator.generate_git_patch(report, str(self.project_path), baseline)
        patch_file = self.project_path / "guidance.patch"
        patch_file.write_text(patch_text, encoding="utf-8")
        applied = subprocess.run(["git", "apply", str(patch_file)], cwd=self.project_path,
                                 text=True, capture_output=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        ast.parse(self.app_file.read_text(encoding="utf-8"))
        self.assertTrue(engine.evaluate(baseline=baseline).has_blocking_errors)
        self.assertNotIn("vibeguard:exempt", patch_text)

    def test_snapshot_non_utf8_encoding_fallback(self):
        # Create non-UTF8 file with Latin-1 bytes
        latin1_file = self.project_path / "legacy_module.py"
        latin1_file.write_bytes(b"# Latin-1 encoding with \xe9\xe8\xe0\ndef legacy_calc(x):\n    return x + 1\n")

        engine = SnapshotEngine(str(self.project_path), self.config)
        snapshot = engine.create_snapshot()
        self.assertIn("legacy_module.py", snapshot.files)
        file_info = snapshot.files["legacy_module.py"]
        sym_names = [s["name"] for s in file_info["symbols"]]
        self.assertIn("legacy_calc", sym_names)

    def test_patch_generation_with_non_utf8_file(self):
        # Create non-UTF8 file with Latin-1 bytes
        latin1_file = self.project_path / "latin1_mod.py"
        latin1_file.write_bytes(b"# Latin-1 author: Ren\xe9\ndef keep_func():\n    return 1\n\ndef old_func(x):\n    return x\n")

        engine = SnapshotEngine(str(self.project_path), self.config)
        snap = engine.create_snapshot()
        engine.save_baseline(snap)

        # Remove old_func
        latin1_file.write_bytes(b"# Latin-1 author: Ren\xe9\ndef keep_func():\n    return 1\n")

        drift_engine = DriftEngine(str(self.project_path), self.config)
        report = drift_engine.evaluate(baseline=snap)
        patch = RemediationGenerator.generate_git_patch(report, str(self.project_path), snap)
        self.assertEqual(patch, "")
        self.assertIn("old_func", RemediationGenerator.generate_ai_prompt(report))


if __name__ == "__main__":
    unittest.main()
