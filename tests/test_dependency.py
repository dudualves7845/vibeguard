"""Unit tests for DependencyChecker."""

import json
import tempfile
import unittest
from pathlib import Path
from vibeguard.dependency import DependencyChecker


class TestDependencyChecker(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_python_stdlib_allowed(self):
        checker = DependencyChecker(str(self.project_path))
        self.assertTrue(checker.check_python_import("json", set()))
        self.assertTrue(checker.check_python_import("os.path", set()))
        self.assertTrue(checker.check_python_import("hashlib", set()))

    def test_python_hallucinated_flagged(self):
        checker = DependencyChecker(str(self.project_path))
        # Non-existent hallucinated package
        self.assertFalse(checker.check_python_import("non_existent_fake_ai_lib", set()))

    def test_npm_dependencies_verified(self):
        pkg_json = self.project_path / "package.json"
        pkg_json.write_text(
            json.dumps({
                "dependencies": {
                    "express": "^4.18.0"
                },
                "devDependencies": {
                    "typescript": "^5.0.0"
                }
            }),
            encoding="utf-8"
        )

        checker = DependencyChecker(str(self.project_path))
        # Builtins
        self.assertTrue(checker.check_js_import("fs", set()))
        self.assertTrue(checker.check_js_import("path", set()))

        # Declared npm packages
        self.assertTrue(checker.check_js_import("express", set()))
        self.assertTrue(checker.check_js_import("typescript", set()))

        # Relative local imports
        self.assertTrue(checker.check_js_import("./utils", set()))
        self.assertTrue(checker.check_js_import("@/components/button", set()))

        # Hallucinated import
        self.assertFalse(checker.check_js_import("super-magic-hallucinated-package", set()))

    def test_pyproject_toml_dependencies(self):
        pyproject = self.project_path / "pyproject.toml"
        pyproject.write_text(
            """[project]
dependencies = [
    "fastapi>=0.100.0",
    "pydantic~=2.5.0",
]
[project.optional-dependencies]
dev = [
    "pytest",
]
""",
            encoding="utf-8"
        )
        checker = DependencyChecker(str(self.project_path))
        self.assertTrue(checker.check_python_import("fastapi", set()))
        self.assertTrue(checker.check_python_import("pydantic", set()))
        self.assertTrue(checker.check_python_import("pytest", set()))
        self.assertFalse(checker.check_python_import("random_fake_lib", set()))

    def test_monorepo_nested_packages(self):
        nested_pkg = self.project_path / "packages" / "frontend"
        nested_pkg.mkdir(parents=True)
        (nested_pkg / "package.json").write_text(
            json.dumps({"dependencies": {"lodash": "^4.17.21"}}),
            encoding="utf-8"
        )
        checker = DependencyChecker(str(self.project_path))
        self.assertTrue(checker.check_js_import("lodash", set()))

    def test_src_directory_local_modules(self):
        src_pkg = self.project_path / "src" / "my_service"
        src_pkg.mkdir(parents=True)
        (src_pkg / "__init__.py").write_text("", encoding="utf-8")
        checker = DependencyChecker(str(self.project_path))
        self.assertTrue(checker.check_python_import("my_service", set()))

    def test_go_dependencies_and_stdlib(self):
        go_mod = self.project_path / "go.mod"
        go_mod.write_text(
            """module github.com/myorg/myapp

go 1.22

require (
    github.com/gorilla/mux v1.8.0
    go.uber.org/zap v1.26.0
)
""",
            encoding="utf-8",
        )
        checker = DependencyChecker(str(self.project_path))
        # Stdlib
        self.assertTrue(checker.check_go_import("fmt", set()))
        self.assertTrue(checker.check_go_import("net/http", set()))
        self.assertTrue(checker.check_go_import("encoding/json", set()))

        # Declared in go.mod
        self.assertTrue(checker.check_go_import("github.com/gorilla/mux", set()))
        self.assertTrue(checker.check_go_import("go.uber.org/zap", set()))
        self.assertTrue(checker.check_go_import("github.com/myorg/myapp/subpkg", set()))

        # Relative import
        self.assertTrue(checker.check_go_import("./internal/config", set()))

        # Hallucinated Go package
        self.assertFalse(checker.check_go_import("github.com/hallucinated/crypto-magic", set()))

    def test_rust_dependencies_and_stdlib(self):
        cargo_toml = self.project_path / "Cargo.toml"
        cargo_toml.write_text(
            """[package]
name = "mycrate"
version = "0.1.0"

[dependencies]
serde = "1.0"
tokio = { version = "1.0", features = ["full"] }

[dev-dependencies]
tempfile = "3.0"
""",
            encoding="utf-8",
        )
        checker = DependencyChecker(str(self.project_path))
        # Stdlib / keywords
        self.assertTrue(checker.check_rust_import("std", set()))
        self.assertTrue(checker.check_rust_import("core", set()))
        self.assertTrue(checker.check_rust_import("crate", set()))

        # Declared in Cargo.toml
        self.assertTrue(checker.check_rust_import("serde", set()))
        self.assertTrue(checker.check_rust_import("tokio", set()))
        self.assertTrue(checker.check_rust_import("tempfile", set()))

        # Hallucinated Rust crate
        self.assertFalse(checker.check_rust_import("hallucinated_quantum_ai", set()))

    def test_canonical_pypi_distribution_mapping(self):
        req_file = self.project_path / "requirements.txt"
        req_file.write_text(
            """PyYAML>=6.0
Pillow~=10.0.0
opencv-python==4.8.0
beautifulsoup4
psycopg2-binary>=2.9.0
scikit-learn
python-dotenv
""",
            encoding="utf-8"
        )
        checker = DependencyChecker(str(self.project_path))
        # Should recognize canonical imports even if not installed in global env
        self.assertTrue(checker.check_python_import("yaml", set()))
        self.assertTrue(checker.check_python_import("PIL", set()))
        self.assertTrue(checker.check_python_import("cv2", set()))
        self.assertTrue(checker.check_python_import("bs4", set()))
        self.assertTrue(checker.check_python_import("psycopg2", set()))
        self.assertTrue(checker.check_python_import("sklearn", set()))
        self.assertTrue(checker.check_python_import("dotenv", set()))
        # And should still reject hallucinated
        self.assertFalse(checker.check_python_import("hallucinated_ai_db", set()))


if __name__ == "__main__":
    unittest.main()
