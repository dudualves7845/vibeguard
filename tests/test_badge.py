"""Comprehensive unit tests for the VibeGuard Badge System.

Tests SVG generation, dynamic state resolution, snippet formatting,
CLI subcommands, Service layer, and HTTP API endpoints.
"""

from io import StringIO
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from vibeguard.badge import (
    COLOR_DRIFT,
    COLOR_UNANCHORED,
    COLOR_VERIFIED,
    DEFAULT_FILENAME,
    DEFAULT_LINK_URL,
    LABEL_PROTECTED,
    LABEL_VIBEGUARD,
    MSG_DRIFT,
    MSG_UNANCHORED,
    MSG_VERIFIED,
    STATE_DRIFT,
    STATE_UNANCHORED,
    STATE_VERIFIED,
    STATE_ERROR,
    BadgeGenerator,
    BadgeState,
    _escape_xml,
    estimate_text_width,
    generate_project_badge_file,
    get_project_state,
    get_state_from_report,
)
from vibeguard.cli import main
from vibeguard.config import GuardConfig
from vibeguard.drift import DriftFinding, DriftReport
from vibeguard.server import create_server
from vibeguard.ui import VibeGuardService


class TestBadgeSVG(unittest.TestCase):
    """Tests for SVG generation, text calculations, and XML validity."""

    def test_verified_badge_svg(self):
        svg = BadgeGenerator.generate_svg(
            label=LABEL_PROTECTED, message=MSG_VERIFIED, color=COLOR_VERIFIED, status=STATE_VERIFIED
        )
        self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', svg)
        self.assertIn('role="img"', svg)
        self.assertIn('aria-label="Protected: No findings"', svg)
        self.assertIn(f'fill="{COLOR_VERIFIED}"', svg)
        self.assertIn("Protected</text>", svg)
        self.assertIn("No findings</text>", svg)
        self.assertIn("M9 12l2 2 4-4", svg)
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_drift_detected_badge_svg(self):
        svg = BadgeGenerator.generate_svg(
            label=LABEL_VIBEGUARD, message=MSG_DRIFT, color=COLOR_DRIFT, status=STATE_DRIFT
        )
        self.assertIn('aria-label="VibeGuard: Drift Detected"', svg)
        self.assertIn(f'fill="{COLOR_DRIFT}"', svg)
        self.assertIn("VibeGuard</text>", svg)
        self.assertIn("Drift Detected</text>", svg)
        self.assertIn("M12 8v5M12 16.5h.01", svg)
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_unanchored_badge_svg(self):
        svg = BadgeGenerator.generate_svg(
            label=LABEL_VIBEGUARD, message=MSG_UNANCHORED, color=COLOR_UNANCHORED, status=STATE_UNANCHORED
        )
        self.assertIn('aria-label="VibeGuard: Unanchored"', svg)
        self.assertIn(f'fill="{COLOR_UNANCHORED}"', svg)
        self.assertIn("Unanchored</text>", svg)
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_badge_without_icon(self):
        svg = BadgeGenerator.generate_svg(label="Custom", message="Status", color="#000000", include_icon=False)
        self.assertNotIn('<g transform="translate(6, 3)', svg)
        self.assertIn("Custom</text>", svg)
        self.assertIn("Status</text>", svg)
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_xml_escaping(self):
        svg = BadgeGenerator.generate_svg(label='<"Foo&Bar">', message="'Rock&Roll'", color="#123456")
        self.assertIn("&lt;&quot;Foo&amp;Bar&quot;&gt;", svg)
        self.assertIn("&apos;Rock&amp;Roll&apos;", svg)
        self.assertEqual(_escape_xml(None), "")
        self.assertEqual(_escape_xml(42), "42")
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_text_width_estimation(self):
        self.assertEqual(estimate_text_width(""), 0)
        self.assertEqual(estimate_text_width(None), 0)
        w_short = estimate_text_width("hi")
        w_long = estimate_text_width("SuperLongContractValidationString")
        self.assertGreater(w_long, w_short)
        self.assertGreaterEqual(w_short, 8)
        self.assertGreater(estimate_text_width("mmm"), estimate_text_width("ttt"))
        self.assertGreater(estimate_text_width("aaa"), estimate_text_width("fff"))


class TestBadgeStateResolution(unittest.TestCase):
    """Tests for dynamic badge state computation."""

    def test_state_from_report_unanchored(self):
        state = get_state_from_report(report=None, has_baseline=False)
        self.assertEqual(state.status, STATE_UNANCHORED)
        self.assertEqual(state.label, LABEL_VIBEGUARD)
        self.assertEqual(state.message, MSG_UNANCHORED)
        self.assertEqual(state.color, COLOR_UNANCHORED)
        self.assertFalse(state.has_baseline)

    def test_state_from_report_verified(self):
        report = DriftReport(total_findings=0, critical_count=0, error_count=0, warning_count=0, has_baseline=True)
        state = get_state_from_report(report=report, has_baseline=True)
        self.assertEqual(state.status, STATE_VERIFIED)
        self.assertEqual(state.label, LABEL_PROTECTED)
        self.assertEqual(state.message, MSG_VERIFIED)
        self.assertEqual(state.color, COLOR_VERIFIED)
        self.assertTrue(state.has_baseline)

    def test_state_from_report_drift(self):
        finding = DriftFinding(
            category="contract", severity="critical", file_path="foo.py", line_number=1,
            title="Function deleted", message="func removed", remediation="restore"
        )
        report = DriftReport(
            total_findings=1, critical_count=1, error_count=0, warning_count=0,
            findings=[finding], has_baseline=True
        )
        state = get_state_from_report(report=report, has_baseline=True)
        self.assertEqual(state.status, STATE_DRIFT)
        self.assertEqual(state.label, LABEL_VIBEGUARD)
        self.assertEqual(state.message, MSG_DRIFT)
        self.assertEqual(state.color, COLOR_DRIFT)
        self.assertEqual(state.findings_count, 1)

    def test_get_project_state_in_temp_project(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            root = Path(tmp_dir)
            self.assertEqual(get_project_state(root).status, STATE_UNANCHORED)

            (root / "mod.py").write_text("def run():\n    return 42\n", encoding="utf-8")
            cfg = GuardConfig(project_name="tmp_proj")
            cfg.save_to_file(str(root / ".vibeguard.json"))

            from vibeguard.snapshot import SnapshotEngine
            SnapshotEngine(str(root), cfg).save_baseline()
            self.assertEqual(get_project_state(root).status, STATE_VERIFIED)

            (root / "mod.py").write_text("# TODO: fix later\ndef run():\n    pass\n", encoding="utf-8")
            self.assertEqual(get_project_state(root).status, STATE_DRIFT)

            (root / ".vibeguard" / "baseline.json").write_text("{corrupt: json", encoding="utf-8")
            self.assertEqual(get_project_state(root).status, STATE_ERROR)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestBadgeSnippetsAndFiles(unittest.TestCase):
    """Tests for snippets and file saving."""

    def test_markdown_snippet(self):
        snip = BadgeGenerator.generate_markdown_snippet()
        self.assertEqual(snip, f"[![VibeGuard Protected]({DEFAULT_FILENAME})]({DEFAULT_LINK_URL})")
        custom = BadgeGenerator.generate_markdown_snippet(
            image_path="docs/b.svg", link_url="https://example.com", alt_text="Custom Alt"
        )
        self.assertEqual(custom, "[![Custom Alt](docs/b.svg)](https://example.com)")

    def test_html_snippet(self):
        snip = BadgeGenerator.generate_html_snippet()
        expected = f'<a href="{DEFAULT_LINK_URL}"><img src="{DEFAULT_FILENAME}" alt="VibeGuard Protected" /></a>'
        self.assertEqual(snip, expected)

    def test_save_badge_creates_file(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            out_file = Path(tmp_dir) / "nested" / "badge.svg"
            BadgeGenerator.save_badge(out_file, "<svg></svg>")
            self.assertTrue(out_file.exists())
            self.assertEqual(out_file.read_text(encoding="utf-8"), "<svg></svg>")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_generate_project_badge_file(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            root = Path(tmp_dir)
            (root / "mod.py").write_text("def hello(): return 1\n", encoding="utf-8")
            res = generate_project_badge_file(project_root=root)
            self.assertIn("status", res)
            self.assertIn("svg", res)
            self.assertTrue(Path(res["output_path"]).exists())
            self.assertEqual(res["relative_path"], DEFAULT_FILENAME)

            rel_res = generate_project_badge_file(project_root=root, output_path="nested/b.svg")
            self.assertTrue(Path(rel_res["output_path"]).exists())
            self.assertEqual(rel_res["relative_path"], "nested/b.svg")
            self.assertTrue((root / "nested" / "b.svg").exists())
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestBadgeCLI(unittest.TestCase):
    """Tests for CLI integration with 'vibeguard badge'."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.root = Path(self.tmp_dir)
        (self.root / "app.py").write_text("def main(): return 0\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_cli(self, args):
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            return main(args), sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

    def test_cli_badge_default(self):
        code, out = self._run_cli(["badge", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("VibeGuard Badge", out)
        self.assertIn("[![VibeGuard Protected]", out)
        self.assertTrue((self.root / DEFAULT_FILENAME).exists())

    def test_cli_badge_custom_output(self):
        custom_out = self.root / "custom-badge.svg"
        code, _ = self._run_cli(["badge", str(self.root), "--output", str(custom_out)])
        self.assertEqual(code, 0)
        self.assertTrue(custom_out.exists())

    def test_cli_badge_relative_output_with_project(self):
        code, out = self._run_cli(["badge", str(self.root), "--output", "assets/rel.svg", "--markdown"])
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "assets" / "rel.svg").exists())
        self.assertIn("assets/rel.svg", out)

    def test_cli_badge_markdown_flag(self):
        code, out = self._run_cli(["badge", str(self.root), "--markdown"])
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().startswith("[![VibeGuard Protected]"))

    def test_cli_badge_html_flag(self):
        code, out = self._run_cli(["badge", str(self.root), "--html"])
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().startswith("<a href="))

    def test_cli_badge_json_flag(self):
        code, out = self._run_cli(["badge", str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("status", data)
        self.assertIn("color", data)
        self.assertIn("svg", data)

    def test_cli_badge_nonexistent_project(self):
        bad_path = self.root / "does_not_exist"
        code, out = self._run_cli(["badge", str(bad_path)])
        self.assertEqual(code, 1)
        self.assertIn("Project directory does not exist", out)


class TestBadgeServiceAndServer(unittest.TestCase):
    """Tests for Dashboard Service layer and HTTP endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.root = Path(cls.test_dir)
        (cls.root / "sample.py").write_text("def test(): return True\n", encoding="utf-8")
        cfg = GuardConfig(project_name="service_badge_test")
        cfg.save_to_file(str(cls.root / ".vibeguard.json"))

        cls.service = VibeGuardService(str(cls.root))
        sock = socket.socket()
        sock.bind(("", 0))
        cls.port = sock.getsockname()[1]
        sock.close()

        cls.server = create_server("127.0.0.1", cls.port, service=cls.service)
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_service_get_badge(self):
        data = self.service.get_badge()
        self.assertIn("status", data)
        self.assertIn("label", data)
        self.assertIn("message", data)
        self.assertIn("color", data)
        self.assertIn("svg", data)
        self.assertIn("output_path", data)
        self.assertTrue(Path(data["output_path"]).exists())

    def test_http_api_badge_json(self):
        url = f"{self.base_url}/api/badge"
        req = urllib.request.Request(url, headers={"X-VibeGuard-Token": self.server.api_token})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), "application/json")
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("status", data)
            self.assertIn("svg", data)

    def test_http_api_badge_post(self):
        url = f"{self.base_url}/api/badge"
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json",
                                                               "X-VibeGuard-Token": self.server.api_token})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("status", data)
            self.assertIn("svg", data)

    def test_http_api_badge_svg_format(self):
        url = f"{self.base_url}/api/badge?format=svg"
        req = urllib.request.Request(url, headers={"X-VibeGuard-Token": self.server.api_token})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), "image/svg+xml")
            self.assertIn("no-cache", resp.headers.get("Cache-Control", ""))
            content = resp.read().decode("utf-8")
            self.assertIn("<svg", content)

    def test_http_api_badge_svg_path(self):
        url = f"{self.base_url}/api/badge.svg"
        req = urllib.request.Request(url, headers={"X-VibeGuard-Token": self.server.api_token})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), "image/svg+xml")
            content = resp.read().decode("utf-8")
            self.assertIn("<svg", content)


if __name__ == "__main__":
    unittest.main()
