"""Comprehensive tests for VibeGuard Web Dashboard, Server, and Service layer."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

from vibeguard.cli import main
from vibeguard.config import GuardConfig
from vibeguard.server import RateLimiter, create_server, ThreadedHTTPServer
from vibeguard.ui import VibeGuardService


class TestVibeGuardService(unittest.TestCase):
    """Tests for VibeGuardService business logic."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = Path(self.test_dir)

        # Initialize git repo in test_dir
        subprocess.run(["git", "init"], cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(["git", "config", "user.email", "test@vibeguard.io"], cwd=str(self.root), check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(self.root), check=True)

        # Create sample files
        self.sample_file = self.root / "sample.py"
        self.sample_file.write_text(
            "import os\nimport sys\nimport hallucinated_fake_lib\n\n"
            "def calculate_tax(amount: float, rate: float = 0.2) -> float:\n"
            "    '''Calculates tax value.'''\n"
            "    return amount * rate\n\n"
            "class InvoiceProcessor:\n"
            "    def process(self, invoice_id: str) -> bool:\n"
            "        return True\n",
            encoding="utf-8",
        )

        # Initialize config
        self.config = GuardConfig(
            project_name="test_proj",
            max_file_lines=400,
            fail_on_hallucinated_imports=True,
        )
        self.config.save_to_file(str(self.root / ".vibeguard.json"))

        self.service = VibeGuardService(str(self.root))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_project_info(self):
        info = self.service.get_project_info()
        self.assertEqual(info["project_name"], "test_proj")
        self.assertEqual(info["project_path"], str(self.root))
        self.assertTrue(info["is_git_repo"])
        self.assertTrue(info["has_config"])
        self.assertFalse(info["has_baseline"])
        self.assertFalse(info["git_hook_installed"])

    def test_get_prompt_without_baseline_returns_unanchored_guidance(self):
        result = self.service.get_prompt()
        self.assertIsInstance(result["prompt"], str)
        self.assertIsInstance(result["summary"], str)
        self.assertFalse(result["has_baseline"])
        self.assertIn("unanchored", result["summary"].lower())

    def test_update_baseline(self):
        res = self.service.update_baseline()
        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["total_files"], 1)

        info = self.service.get_project_info()
        self.assertTrue(info["has_baseline"])
        self.assertIsNotNone(info["baseline"])
        self.assertGreaterEqual(info["baseline"]["total_files"], 1)

    def test_run_check(self):
        self.service.update_baseline()
        check_res = self.service.run_check()
        self.assertIn("report", check_res)
        self.assertIn("prompt", check_res)
        self.assertIn("summary", check_res)

        # Hallucinated import 'hallucinated_fake_lib' should be detected
        report = check_res["report"]
        self.assertGreater(report["total_findings"], 0)
        found_hallucination = any(f["category"] == "hallucinated_import" for f in report["findings"])
        self.assertTrue(found_hallucination)

    def test_select_project(self):
        sub_dir = self.root / "sub_project"
        sub_dir.mkdir()
        (sub_dir / "index.js").write_text("console.log('hello');", encoding="utf-8")

        res = self.service.select_project(f"  {sub_dir}  ")
        self.assertEqual(res["project_path"], str(sub_dir))
        self.assertEqual(str(self.service.project_root), str(sub_dir))

        # Test selecting invalid and empty paths
        with self.assertRaises(ValueError):
            self.service.select_project(str(self.root / "does_not_exist"))
        with self.assertRaises(ValueError):
            self.service.select_project("")
        with self.assertRaises(ValueError):
            self.service.select_project("   ")

    def test_manage_hook(self):
        # Install hook
        install_res = self.service.manage_hook(action="install")
        self.assertTrue(install_res["success"])
        self.assertTrue(install_res["installed"])
        self.assertTrue(self.service.git_helper.is_hook_installed())

        # Uninstall hook
        uninstall_res = self.service.manage_hook(action="uninstall")
        self.assertTrue(uninstall_res["success"])
        self.assertFalse(uninstall_res["installed"])
        self.assertFalse(self.service.git_helper.is_hook_installed())

    def test_get_symbols(self):
        sym_data = self.service.get_symbols()
        self.assertIn("total_symbols", sym_data)
        self.assertIn("files", sym_data)
        self.assertGreaterEqual(sym_data["total_symbols"], 2)

        # Check sample.py symbols
        sample_entry = next((f for f in sym_data["files"] if f["path"] == "sample.py"), None)
        self.assertIsNotNone(sample_entry)
        symbol_names = [s["name"] for s in sample_entry["symbols"]]
        self.assertIn("calculate_tax", symbol_names)
        self.assertIn("InvoiceProcessor", symbol_names)

    def test_get_dependencies(self):
        deps_data = self.service.get_dependencies()
        self.assertIn("total_imports", deps_data)
        self.assertIn("verified", deps_data)
        self.assertIn("hallucinated", deps_data)

        # 'os' and 'sys' should be verified stdlib
        verified_names = [d["name"] for d in deps_data["verified"]]
        self.assertIn("os", verified_names)
        self.assertIn("sys", verified_names)

        # 'hallucinated_fake_lib' should be hallucinated
        hallucinated_names = [d["name"] for d in deps_data["hallucinated"]]
        self.assertIn("hallucinated_fake_lib", hallucinated_names)

    def test_update_config(self):
        update_res = self.service.update_config({
            "max_file_lines": 350,
            "fail_on_dangerous_calls": True,
        })
        self.assertTrue(update_res["success"])
        self.assertEqual(self.service.config.max_file_lines, 350)
        self.assertTrue(self.service.config.fail_on_dangerous_calls)

        # Verify persisted on disk
        loaded = GuardConfig.load_from_file(str(self.root / ".vibeguard.json"))
        self.assertEqual(loaded.max_file_lines, 350)
        self.assertTrue(loaded.fail_on_dangerous_calls)

    def test_get_prompt_and_patch(self):
        self.service.update_baseline()
        prompt_res = self.service.get_prompt()
        self.assertIn("prompt", prompt_res)
        self.assertIn("total_findings", prompt_res)

        patch_res = self.service.generate_patch()
        self.assertIn("patch", patch_res)
        self.assertIn("has_patch", patch_res)


class TestHTTPServerIntegration(unittest.TestCase):
    """End-to-end integration tests over local HTTP socket."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.root = Path(cls.test_dir)

        # Git init
        subprocess.run(["git", "init"], cwd=str(cls.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(["git", "config", "user.email", "test@vibeguard.io"], cwd=str(cls.root), check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(cls.root), check=True)

        sample = cls.root / "math_utils.py"
        sample.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

        config = GuardConfig(project_name="server_test_proj")
        config.save_to_file(str(cls.root / ".vibeguard.json"))

        cls.service = VibeGuardService(str(cls.root))
        cls.service.update_baseline()

        # Bind to port 0 for an ephemeral available port
        cls.server = create_server(host="127.0.0.1", port=0, service=cls.service)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def _http_get(self, path: str):
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers={"X-VibeGuard-Token": self.server.api_token})
        with urllib.request.urlopen(req) as resp:
            content = resp.read()
            return resp.status, resp.headers.get_content_type(), content

    def _http_post(self, path: str, body: dict):
        url = f"{self.base_url}{path}"
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "X-VibeGuard-Token": self.server.api_token})
        with urllib.request.urlopen(req) as resp:
            content = resp.read()
            return resp.status, resp.headers.get_content_type(), json.loads(content.decode("utf-8"))

    def test_static_assets(self):
        # Index HTML
        status, ctype, content = self._http_get("/")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "text/html")
        self.assertIn(b"VibeGuard", content)

        # CSS
        status, ctype, content = self._http_get("/style.css")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "text/css")
        self.assertIn(b"--bg-primary", content)

        # JS
        status, ctype, content = self._http_get("/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/javascript")
        self.assertIn(b"VibeGuard", content)

        # API Client JS
        status, ctype, content = self._http_get("/api.js")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/javascript")
        self.assertIn(b"vgApi", content)

    def test_api_project(self):
        status, ctype, content = self._http_get("/api/project")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/json")
        data = json.loads(content.decode("utf-8"))
        self.assertEqual(data["project_name"], "server_test_proj")
        self.assertTrue(data["has_baseline"])

    def test_api_check(self):
        status, ctype, data = self._http_post("/api/check", {"diff": False, "patch": True, "sarif": True})
        self.assertEqual(status, 200)
        self.assertIn("report", data)
        self.assertIn("prompt", data)
        self.assertIn("sarif", data)

    def test_api_baseline(self):
        status, ctype, data = self._http_post("/api/baseline", {})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    def test_api_hook(self):
        status, ctype, data = self._http_post("/api/hook", {"action": "install"})
        self.assertEqual(status, 200)
        self.assertTrue(data["installed"])

        # Uninstall
        status, ctype, data = self._http_post("/api/hook", {"action": "uninstall"})
        self.assertEqual(status, 200)
        self.assertFalse(data["installed"])

    def test_api_prompt_and_patch(self):
        status, ctype, content = self._http_get("/api/prompt")
        self.assertEqual(status, 200)
        data = json.loads(content.decode("utf-8"))
        self.assertIn("prompt", data)

        status, ctype, data = self._http_post("/api/patch", {})
        self.assertEqual(status, 200)
        self.assertIn("patch", data)

    def test_api_rate_limiter_returns_http_429_after_limit(self):
        original_limiter = self.server.rate_limiter
        self.server.rate_limiter = RateLimiter(max_requests=1, window_secs=60)
        try:
            status, _, _ = self._http_get("/api/project")
            self.assertEqual(status, 200)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self._http_get("/api/project")
            self.assertEqual(raised.exception.code, 429)
            raised.exception.close()
        finally:
            self.server.rate_limiter = original_limiter

    def test_api_symbols_and_dependencies(self):
        status, ctype, content = self._http_get("/api/symbols")
        self.assertEqual(status, 200)
        data = json.loads(content.decode("utf-8"))
        self.assertIn("total_symbols", data)

        status, ctype, content = self._http_get("/api/dependencies")
        self.assertEqual(status, 200)
        data = json.loads(content.decode("utf-8"))
        self.assertIn("verified", data)

    def test_api_config(self):
        status, ctype, data = self._http_post("/api/config", {"max_file_lines": 380})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["config"]["max_file_lines"], 380)

    def test_api_select_project(self):
        # Invalid project
        url = f"{self.base_url}/api/select-project"
        payload = json.dumps({"path": "/invalid/non/existent/path"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "X-VibeGuard-Token": self.server.api_token})
        try:
            with urllib.request.urlopen(req) as resp:
                self.fail("Expected HTTPError 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            e.close()

    def test_favicon_and_cors(self):
        # Favicon returns 204
        with urllib.request.urlopen(f"{self.base_url}/favicon.ico") as resp:
            self.assertEqual(resp.status, 204)

        # CORS preflight OPTIONS returns 204
        req = urllib.request.Request(f"{self.base_url}/api/project", method="OPTIONS", headers={"Origin": self.base_url})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 204)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), self.base_url)

    def test_server_port_fallback(self):
        s1 = create_server(host="127.0.0.1", port=0)
        p = s1.server_address[1]
        try:
            s2 = create_server(host="127.0.0.1", port=p, allow_fallback_port=True)
            self.assertNotEqual(s2.server_address[1], p)
            s2.server_close()
        finally:
            s1.server_close()


class TestCLISubcommands(unittest.TestCase):
    """Test CLI argument parsing for ui and dashboard."""

    def test_cli_ui_and_dashboard_parser(self):
        from unittest.mock import patch
        from vibeguard.cli import main

        with patch("vibeguard.server.run_server") as mock_run:
            ret = main(["ui", "--port", "9090", "--host", "0.0.0.0", "--no-browser"])
            self.assertEqual(ret, 0)
            mock_run.assert_called_once_with(host="0.0.0.0", port=9090, open_browser=False, project_path=None)

        with patch("vibeguard.server.run_server") as mock_run:
            ret = main(["dashboard", "/custom/path", "--port", "8686"])
            self.assertEqual(ret, 0)
            mock_run.assert_called_once_with(host="127.0.0.1", port=8686, open_browser=True, project_path="/custom/path")

        ret_help = main([])
        self.assertEqual(ret_help, 0)


if __name__ == "__main__":
    unittest.main()
