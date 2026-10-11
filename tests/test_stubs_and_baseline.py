"""Unit tests for empty function/method stub detection across JS/TS, Go, and Rust,
and baseline status messaging in CLI and drift engine.
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from vibeguard.analyzer import CodeAnalyzer
from vibeguard.cli import _print_report, main
from vibeguard.config import GuardConfig
from vibeguard.drift import DriftEngine, DriftReport
from vibeguard.parser_go import GoParser
from vibeguard.parser_js import JavaScriptTypeScriptParser
from vibeguard.parser_rust import RustParser
from vibeguard.snapshot import ProjectSnapshot, SnapshotEngine


class TestEmptyStubDetection(unittest.TestCase):
    """Tests for empty function/method stub detection in JS/TS, Go, and Rust."""

    def test_js_empty_functions_and_methods(self):
        js_code = """
function emptyClassic() {}

function emptyWithComments() {
    // only a single comment
    /* multi
       line comment */
}

const emptyArrow = () => {};

export const emptyArrowWithComments = async (a, b) => {
    // nothing inside
};

class Service {
    emptyMethod() {}

    public async emptyMethodWithComments(x: number): Promise<void> {
        // comment only
    }

    realMethod() {
        return 42;
    }
}

function realFunction() {
    console.log("hello");
}
"""
        analysis = CodeAnalyzer.analyze_file("service.ts", js_code)
        self.assertEqual(analysis.language, "typescript")

        stubs = [p for p in analysis.placeholders if p.get("kind") == "empty_stub"]
        stub_texts = [s["text"] for s in stubs]

        self.assertTrue(any("emptyClassic" in t for t in stub_texts))
        self.assertTrue(any("emptyWithComments" in t for t in stub_texts))
        self.assertTrue(any("emptyArrow" in t for t in stub_texts))
        self.assertTrue(any("emptyArrowWithComments" in t for t in stub_texts))
        self.assertTrue(any("emptyMethod" in t for t in stub_texts))
        self.assertTrue(any("emptyMethodWithComments" in t for t in stub_texts))

        # Real methods must NOT be flagged
        self.assertFalse(any("realMethod" in t for t in stub_texts))
        self.assertFalse(any("realFunction" in t for t in stub_texts))

    def test_go_empty_functions_and_methods(self):
        go_code = """
package main

func EmptyFunc() {}

func EmptyFuncWithComments(x int) {
    // comment only
    /* block comment */
}

func (s *Server) EmptyMethod() {}

func (s Server) EmptyValueReceiver() (string, error) {
    // comment only
}

func RealFunc() string {
    return "ok"
}

func (s *Server) RealMethod() int {
    return 123
}
"""
        analysis = CodeAnalyzer.analyze_file("main.go", go_code)
        self.assertEqual(analysis.language, "go")

        stubs = [p for p in analysis.placeholders if p.get("kind") == "empty_stub"]
        stub_texts = [s["text"] for s in stubs]

        self.assertTrue(any("EmptyFunc" in t for t in stub_texts))
        self.assertTrue(any("EmptyFuncWithComments" in t for t in stub_texts))
        self.assertTrue(any("Server.EmptyMethod" in t for t in stub_texts))
        self.assertTrue(any("Server.EmptyValueReceiver" in t for t in stub_texts))

        # Real funcs must NOT be flagged
        self.assertFalse(any("RealFunc" in t for t in stub_texts))
        self.assertFalse(any("RealMethod" in t for t in stub_texts))

    def test_rust_empty_functions_and_methods(self):
        rust_code = """
fn empty_func() {}

pub fn empty_pub_func(x: i32) {
    // only comments
    /* block comment */
}

impl Engine {
    fn empty_method(&self) {}

    pub fn empty_pub_method(&mut self) {
        // empty
    }

    fn real_method(&self) -> bool {
        true
    }
}

fn real_func() {
    println!("hello");
}
"""
        analysis = CodeAnalyzer.analyze_file("lib.rs", rust_code)
        self.assertEqual(analysis.language, "rust")

        stubs = [p for p in analysis.placeholders if p.get("kind") == "empty_stub"]
        stub_texts = [s["text"] for s in stubs]

        self.assertTrue(any("empty_func" in t for t in stub_texts))
        self.assertTrue(any("empty_pub_func" in t for t in stub_texts))
        self.assertTrue(any("Engine.empty_method" in t for t in stub_texts))
        self.assertTrue(any("Engine.empty_pub_method" in t for t in stub_texts))

        # Real funcs must NOT be flagged
        self.assertFalse(any("real_method" in t for t in stub_texts))
        self.assertFalse(any("real_func" in t for t in stub_texts))


class TestBaselineStatusFeedback(unittest.TestCase):
    """Tests for baseline status messaging in CLI and DriftEngine."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config = GuardConfig(
            project_name="baseline_test",
            protected_paths=["*.py"],
            max_file_lines=200,
        )
        self.config.save_to_file(str(self.root / ".vibeguard.json"))

        self.code_file = self.root / "app.py"
        self.code_file.write_text(
            "def calculate(a, b):\n    return a + b\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_evaluate_without_baseline(self):
        engine = DriftEngine(str(self.root), self.config)
        report = engine.evaluate(baseline=None)

        self.assertFalse(report.has_baseline)
        self.assertIsNone(report.baseline_timestamp)
        self.assertEqual(report.baseline_files_count, 0)
        self.assertEqual(report.baseline_symbols_count, 0)
        self.assertEqual(report.total_findings, 0)

        # Capture CLI output
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            _print_report(report)
        finally:
            sys.stdout = old_stdout

        output = buf.getvalue()
        self.assertIn("No baseline snapshot found", output)
        self.assertIn("unanchored", output)
        self.assertIn("vibeguard baseline", output)

    def test_evaluate_with_baseline_zero_drift(self):
        snapshot_engine = SnapshotEngine(str(self.root), self.config)
        snapshot = snapshot_engine.create_snapshot()
        snapshot_engine.save_baseline(snapshot)

        drift_engine = DriftEngine(str(self.root), self.config)
        baseline = snapshot_engine.load_baseline()
        report = drift_engine.evaluate(baseline=baseline)

        self.assertTrue(report.has_baseline)
        self.assertEqual(report.total_findings, 0)
        self.assertGreaterEqual(report.baseline_files_count, 1)
        self.assertGreaterEqual(report.baseline_symbols_count, 1)

        # Capture CLI output
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            _print_report(report)
        finally:
            sys.stdout = old_stdout

        output = buf.getvalue()
        self.assertIn("0 drift findings", output)
        self.assertIn("relative to baseline", output)
        self.assertIn("symbols across", output)
        self.assertIn("No findings in the selected audit scope", output)

    def test_cli_check_command_messaging(self):
        old_cwd = os.getcwd()
        try:
            os.chdir(str(self.root))
            buf = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = buf
                code = main(["check"])
            finally:
                sys.stdout = old_stdout

            self.assertEqual(code, 0)
            output = buf.getvalue()
            # Without baseline, notice must be displayed
            self.assertIn("No baseline snapshot found", output)

            # Now capture baseline
            main(["baseline"])

            buf2 = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = buf2
                code = main(["check"])
            finally:
                sys.stdout = old_stdout

            self.assertEqual(code, 0)
            output2 = buf2.getvalue()
            self.assertIn("0 drift findings", output2)
            self.assertIn("relative to baseline", output2)
        finally:
            os.chdir(old_cwd)

    def test_cli_target_directory_arguments(self):
        target_dir = Path(tempfile.mkdtemp())
        try:
            # 1. init with positional target
            code = main(["init", str(target_dir)])
            self.assertEqual(code, 0)
            self.assertTrue((target_dir / ".vibeguard.json").exists())
            self.assertTrue((target_dir / ".vibeguard" / "baseline.json").exists())
            initialized_config = json.loads((target_dir / ".vibeguard.json").read_text(encoding="utf-8"))
            self.assertTrue({"*.go", "*.rs", "*.java"}.issubset(set(initialized_config["protected_paths"])))

            # 2. check with positional target
            buf = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = buf
                code = main(["check", str(target_dir)])
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            self.assertIn("0 drift findings", buf.getvalue())

            # 3. check with --project flag
            buf2 = io.StringIO()
            try:
                sys.stdout = buf2
                code = main(["check", "--project", str(target_dir)])
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            self.assertIn("0 drift findings", buf2.getvalue())

            # 4. baseline with positional target
            code = main(["baseline", str(target_dir)])
            self.assertEqual(code, 0)

            # 5. prompt with positional target
            buf3 = io.StringIO()
            try:
                sys.stdout = buf3
                code = main(["prompt", str(target_dir)])
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
        finally:
            import shutil
            shutil.rmtree(target_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
