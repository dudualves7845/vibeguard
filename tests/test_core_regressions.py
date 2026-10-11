"""Regression coverage for contracts, lexical analysis and configuration."""

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vibeguard.analyzer import CodeAnalyzer
from vibeguard.config import GuardConfig, IntentRule
from vibeguard.dependency import DependencyChecker
from vibeguard.drift import DriftEngine
from vibeguard.snapshot import ProjectSnapshot, SnapshotEngine
from vibeguard.parser_utils import mask_noncode


class CoreRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = GuardConfig(project_name="regression", ignored_paths=[], fail_on_hallucinated_imports=False)

    def change(self, name, before, after):
        path = self.root / name
        path.write_text(before, encoding="utf-8")
        baseline = SnapshotEngine(str(self.root), self.config).create_snapshot()
        path.write_text(after, encoding="utf-8")
        return DriftEngine(str(self.root), self.config).evaluate(baseline=baseline)

    def test_commented_declarations_cannot_satisfy_contracts(self):
        for name, before, after in [
            ("api.js", "export function ping(x) { return x; }", "// export function ping(x) { return x; }"),
            ("api.go", "package api\nfunc Ping(x int) int { return x }", "package api\n/*\nfunc Ping(x int) int { return x }\n*/"),
            ("api.rs", "pub fn ping(x: i32) -> i32 { x }", "/*\npub fn ping(x: i32) -> i32 { x }\n*/"),
        ]:
            with self.subTest(language=name):
                self.assertTrue(any(f.category == "api_breakage" for f in self.change(name, before, after).findings))

    def test_string_declarations_do_not_create_contracts(self):
        for name, source in [("api.js", 'const x = "export function ping(x) { return x; }";'),
                             ("api.go", 'package api\nvar x = `\nfunc Ping(x int) int { return x }\n`'),
                             ("api.rs", 'const X: &str = r#"\npub fn ping(x: i32) -> i32 { x }\n"#;')]:
            with self.subTest(language=name):
                self.assertEqual(CodeAnalyzer.analyze_file(name, source).symbols, [])

    def test_javascript_regex_literals_do_not_hide_following_exports(self):
        for literal in ['/"/g', '/[{}]/g', '/[/"{}]/g', r'/\/\*/g', r'/[\\\]\/"]/g']:
            with self.subTest(literal=literal):
                source = 'const expression = ' + literal + ';\nexport function ping() { return 1; }\n'
                self.assertEqual([s.name for s in CodeAnalyzer.analyze_file("api.js", source).symbols], ["ping"])
                self.assertEqual(mask_noncode(literal, "javascript").strip(), "")

    def test_javascript_regex_after_return_or_control_condition_preserves_scope(self):
        source = '''export class API {
    matches(x) {
        if (x) /["{}]/.test(x);
        return /["{}]/.test(x);
    }
    next() { return 1; }
}
export function ping() { return 1; }
'''
        self.assertEqual([s.name for s in CodeAnalyzer.analyze_file("api.js", source).symbols], ["API", "API.matches", "API.next", "ping"])

    def test_javascript_regex_after_for_await_condition_preserves_exports(self):
        for header in ['for await (const value of values)', 'for /* comment */ await /* comment */ (const value of values)']:
            with self.subTest(header=header):
                source = header + ' /"/.test(value);\nexport function ping() { return 1; }\n'
                self.assertEqual([s.name for s in CodeAnalyzer.analyze_file("api.js", source).symbols], ["ping"])

    def test_javascript_regex_after_restricted_statement_newline_preserves_exports(self):
        for statement in ['break', 'continue']:
            for separator in ['\n', ' // comment\n', ' /* comment\n */ ', ' /* comment */\n']:
                with self.subTest(statement=statement, separator=separator):
                    source = 'while (true) { ' + statement + separator + '/"/.test(value); }\nexport function ping() { return 1; }\n'
                    self.assertEqual([s.name for s in CodeAnalyzer.analyze_file("api.js", source).symbols], ["ping"])

    def test_restricted_statement_scope_does_not_change_later_division(self):
        source = 'while (true) { break } const ratio = total\n / count / factor;'
        self.assertEqual(mask_noncode(source, "javascript"), source)

    def test_javascript_block_comments_remain_comments_beside_regexes(self):
        source = '/* TODO fix / and "quotes" */\nconst quote = /"/g;\nexport function ping() { return 1; }\n'
        analysis = CodeAnalyzer.analyze_file("api.js", source)
        self.assertEqual([s.name for s in analysis.symbols], ["ping"])
        self.assertTrue(any(p["kind"] == "comment_marker" for p in analysis.placeholders))

    def test_javascript_division_remains_code(self):
        for expression in ['total / count', 'total / 2 / factor', '(total + 1) / count',
                           'getTotal() / count', 'total /= count', '({value: 1}).value / 2',
                           'total / "/".length', 'object.return / count / factor',
                           'object.if(total) / count / factor', '(function() { return 1; }) / 2 / factor',
                           '(class {}) / 2 / factor']:
            with self.subTest(expression=expression):
                source = 'const ratio = ' + expression + ';\nexport function ping() { return ratio; }\n'
                self.assertEqual([s.name for s in CodeAnalyzer.analyze_file("api.js", source).symbols], ["ping"])
                self.assertEqual(mask_noncode('const ratio = ' + expression + ';', "javascript", mask_strings=False), 'const ratio = ' + expression + ';')

    def test_losing_public_visibility_breaks_contracts(self):
        for name, before, after in [
            ("api.js", "export function ping(x) { return x; }", "function ping(x) { return x; }"),
            ("App.java", "public class App {\n public int ping(int x) { return x; }\n}", "public class App {\n private int ping(int x) { return x; }\n}"),
        ]:
            with self.subTest(language=name):
                self.assertTrue(any(f.category == "api_breakage" and f.severity == "error" for f in self.change(name, before, after).findings))

    def test_symbol_kind_change_breaks_contract(self):
        report = self.change("api.py", "class API:\n value = 1\n", "def API():\n return 1\n")
        self.assertTrue(report.has_blocking_errors)

    def test_python_default_and_parameter_kind_changes_are_detected(self):
        for before, after in [("def ping(x=1):\n return x", "def ping(x):\n return x"),
                              ("def ping(x):\n return x", "def ping(x, /):\n return x"),
                              ("def ping(*, x):\n return x", "def ping(x):\n return x")]:
            with self.subTest(before=before):
                self.assertTrue(any(f.category == "api_breakage" for f in self.change("api.py", before, after).findings))

    def test_java_overload_removal_is_detected(self):
        report = self.change("App.java", "public class App {\n public int ping() { return 1; }\n public int ping(int x) { return x; }\n}",
                             "public class App {\n public int ping(int x) { return x; }\n}")
        self.assertTrue(report.has_blocking_errors)

    def test_disabled_secret_and_ghost_flags_keep_nonblocking_findings(self):
        source = 'api_key = "B7d2Mq9X4nR6tW3y8Ac1Ef5Gh0JkLpZv"\ndef ping():\n pass\n'
        (self.root / "api.py").write_text(source)
        config = GuardConfig(project_name="flags", fail_on_secret_leak=False, fail_on_ghost_declarations=False)
        report = DriftEngine(str(self.root), config).evaluate()
        self.assertEqual({f.category for f in report.findings}, {"secret_leak", "stub_injected"})
        self.assertFalse(report.has_blocking_errors)
        self.assertTrue(all(f.severity == "warning" for f in report.findings))

    def test_forbidden_from_import_submodule_is_detected(self):
        (self.root / "api.py").write_text("from unittest import mock\n")
        config = GuardConfig(project_name="rules", rules=[IntentRule(id="no-mock", description="no mock", rule_type="forbidden_import", parameters={"forbidden": ["unittest.mock"]})])
        self.assertTrue(DriftEngine(str(self.root), config).evaluate().has_blocking_errors)

    def test_stubs_with_docstrings_and_unimplemented_exception_are_detected(self):
        for source in ['def ping():\n """Docs."""\n pass\n', 'class API:\n """Docs."""\n ...\n', 'def ping():\n raise NotImplementedError("pending")\n']:
            with self.subTest(source=source):
                self.assertTrue(CodeAnalyzer.analyze_file("api.py", source).placeholders)

    def test_docstring_only_function_is_still_unimplemented(self):
        self.assertTrue(CodeAnalyzer.analyze_file("api.py", 'def ping():\n """Docs only."""\n').placeholders)

    def test_abstract_and_overload_declarations_are_not_stubs(self):
        source = '''from abc import ABC, abstractmethod
from typing import overload
class API(ABC):
    @abstractmethod
    def ping(self):
        pass
@overload
def value(x: int) -> int: ...
@overload
def value(x: str) -> str: ...
def value(x):
    return x
'''
        self.assertEqual(CodeAnalyzer.analyze_file("api.py", source).placeholders, [])

    def test_property_setter_is_not_duplicate_declaration(self):
        source = '''class API:
    @property
    def value(self):
        return self._value
    @value.setter
    def value(self, value):
        self._value = value
'''
        self.assertEqual(CodeAnalyzer.analyze_file("api.py", source).placeholders, [])

    def test_overload_defaults_still_receive_security_checks(self):
        source = '''from typing import overload
@overload
def value(x=eval("1")): ...
def value(x=1):
    return x
'''
        self.assertEqual([call["call"] for call in CodeAnalyzer.analyze_file("api.py", source).dangerous_calls], ["eval"])

    def test_secret_in_dictionary_key_is_detected(self):
        source = 'config = {"api_key": "B7d2Mq9X4nR6tW3y8Ac1Ef5Gh0JkLpZv"}'
        self.assertEqual(len(CodeAnalyzer.analyze_file("api.py", source).secret_findings), 1)

    def test_rust_braces_in_literals_and_comments_do_not_change_method_scope(self):
        source = '''pub struct API;
impl API {
    pub fn value(&self) -> &'static str {
        "{"
    }
    // { another brace
}
pub fn ping() -> u8 { 1 }
'''
        self.assertIn("ping", [s.name for s in CodeAnalyzer.analyze_file("api.rs", source).symbols])

    def test_url_with_todo_in_string_is_not_placeholder(self):
        source = 'URL = "https://example.com/todo"\nmessage = "# TODO this is a string"\n'
        self.assertEqual(CodeAnalyzer.analyze_file("api.py", source).placeholders, [])
        self.assertTrue(CodeAnalyzer.analyze_file("api.py", 'URL = "https://example.com"  # TODO fix\n').placeholders)

    def test_inline_java_annotation_does_not_hide_method(self):
        source = 'public class App {\n @Override public int ping(int x) { return x; }\n}\n'
        self.assertIn("App.ping", [s.name for s in CodeAnalyzer.analyze_file("App.java", source).symbols])

    def test_java_empty_stubs_are_detected_but_abstract_declarations_are_not(self):
        source = 'public abstract class App {\n public void ping() { /* pending */ }\n public abstract void good();\n}\n'
        stubs = CodeAnalyzer.analyze_file("App.java", source).placeholders
        self.assertEqual(len(stubs), 1)
        self.assertIn("ping", stubs[0]["text"])

    def test_partial_configuration_preserves_default_ignored_paths(self):
        self.assertEqual(GuardConfig.from_dict({"project_name": "x"}).ignored_paths, GuardConfig(project_name="x").ignored_paths)

    def test_configuration_rejects_invalid_values_before_saving(self):
        for data in [{"max_file_lines": None}, {"max_file_lines": True}, {"max_file_lines": 0},
                     {"max_cyclomatic_complexity": -1}, {"fail_on_secret_leak": "false"},
                     {"protected_paths": "*.py"}, {"ignored_paths": [1]}, {"project_name": None},
                     {"rules": [None]}, {"rules": [{"severity": "fatal"}]},
                     {"rules": [{"id": ""}]}, {"rules": [{"description": "  "}]},
                     {"rules": [{"parameters": []}]}, {"rules": [{"target_patterns": "*.py"}]},
                     {"rules": [{"rule_type": "max_lines", "parameters": {"max": None}}]},
                     {"rules": [{"rule_type": "forbidden_import", "parameters": {"forbidden": "mock"}}]}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                GuardConfig.from_dict(data).save_to_file(str(self.root / "config.json"))

    def test_unserializable_rule_parameters_do_not_truncate_previous_configuration(self):
        path = self.root / "config.json"
        path.write_text('{"project_name":"previous"}')
        config = GuardConfig(project_name="new", rules=[IntentRule(id="custom", description="custom", rule_type="custom", parameters={"value": object()})])
        with self.assertRaises((TypeError, ValueError)):
            config.save_to_file(str(path))
        self.assertEqual(path.read_text(), '{"project_name":"previous"}')

    def test_current_snapshot_can_be_supplied_for_proposed_code(self):
        snapshot = ProjectSnapshot(timestamp=1, total_files=1, files={"api.py": asdict(CodeAnalyzer.analyze_file("api.py", "def ping():\n pass\n"))})
        try:
            report = DriftEngine(str(self.root), self.config).evaluate_snapshot(snapshot)
        except TypeError as exc:
            self.fail("current_snapshot must be accepted: " + str(exc))
        self.assertTrue(report.has_blocking_errors)

    def test_legacy_baseline_remains_usable(self):
        baseline = ProjectSnapshot(timestamp=1, total_files=1, files={"api.py": {"symbols": [{"name": "ping", "kind": "function", "is_exported": True, "parameters": ["x"]}]}})
        (self.root / "api.py").write_text("def ping(x):\n return x\n")
        self.assertEqual(DriftEngine(str(self.root), self.config).evaluate(baseline=baseline).findings, [])


class DependencyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_requirements_extras_and_comparison_operators_are_normalized(self):
        with patch("importlib.metadata.distributions", return_value=[]):
            for requirement in ["requests[socks]>=2.31", "requests>2.31", "requests<3", "requests===2.31"]:
                with self.subTest(requirement=requirement):
                    (self.root / "requirements.txt").write_text(requirement)
                    self.assertTrue(DependencyChecker(str(self.root)).check_python_import("requests", set()))

    def test_installed_distribution_mapping_matches_import_name(self):
        class Distribution:
            metadata = {"Name": "PyYAML"}
        with patch("importlib.metadata.distributions", return_value=[Distribution()]):
            self.assertTrue(DependencyChecker(str(self.root)).check_python_import("yaml", set()))

    def test_declared_namespace_distribution_accepts_its_module(self):
        (self.root / "requirements.txt").write_text("protobuf>=4")
        self.assertTrue(DependencyChecker(str(self.root)).check_python_import("google.protobuf", set()))

    def test_target_specific_rust_dependency_is_declared(self):
        (self.root / "Cargo.toml").write_text('[package]\nname="app"\nversion="0.1.0"\n[target.\'cfg(unix)\'.dependencies]\nlibc="0.2"\n')
        self.assertTrue(DependencyChecker(str(self.root)).check_rust_import("libc", set()))

    def test_node_builtin_subpath_does_not_require_npm_dependency(self):
        self.assertTrue(DependencyChecker(str(self.root)).check_js_import("stream/promises", set()))


if __name__ == "__main__":
    unittest.main()
