"""Comprehensive tests for VibeGuard GitHub Action & CI Runner."""

from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from typing import Any
import urllib.error

from vibeguard.cli import main
from vibeguard.config import GuardConfig
from vibeguard.drift import DriftFinding, DriftReport
from vibeguard.github_action import (
    DEFAULT_BASELINE_FILE,
    DEFAULT_PATCH_FILE,
    STICKY_COMMENT_MARKER,
    detect_pr_context,
    escape_markdown_cell,
    github_api_request,
    post_or_update_pr_comment,
    render_summary_markdown,
    run_ci_audit,
    str_to_bool,
    write_github_outputs,
    write_step_summary,
)
from vibeguard.snapshot import ProjectSnapshot, SnapshotEngine


class TestGitHubActionHelpers(unittest.TestCase):
    """Test helper utilities for GitHub Action CI."""

    def test_str_to_bool(self):
        self.assertTrue(str_to_bool(True))
        self.assertFalse(str_to_bool(False))
        self.assertTrue(str_to_bool(None, default=True))
        self.assertFalse(str_to_bool(None, default=False))

        for truthy in ("true", "True", "TRUE", "1", "yes", "YES", "on"):
            self.assertTrue(str_to_bool(truthy))

        for falsy in ("false", "False", "0", "no", "off", "random"):
            self.assertFalse(str_to_bool(falsy))

    def test_escape_markdown_cell(self):
        raw = "foo | bar\r\nbaz\nqux"
        escaped = escape_markdown_cell(raw)
        self.assertEqual(escaped, "foo \\| bar<br>baz<br>qux")

    def test_detect_pr_context_overrides(self):
        repo, pr = detect_pr_context(repo_override="owner/my-repo", pr_override=42)
        self.assertEqual(repo, "owner/my-repo")
        self.assertEqual(pr, 42)

    def test_detect_pr_context_from_env(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "octocat/hello-world", "PR_NUMBER": "99"}):
            repo, pr = detect_pr_context()
            self.assertEqual(repo, "octocat/hello-world")
            self.assertEqual(pr, 99)

        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "octocat/hello-world", "GITHUB_PR_NUMBER": "100"}, clear=True):
            repo, pr = detect_pr_context()
            self.assertEqual(repo, "octocat/hello-world")
            self.assertEqual(pr, 100)

    def test_detect_pr_context_from_event_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ev_path = Path(tmpdir) / "event.json"
            ev_data = {
                "pull_request": {"number": 123},
                "repository": {"full_name": "org/repo-from-event"},
            }
            ev_path.write_text(json.dumps(ev_data), encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                repo, pr = detect_pr_context(event_path=str(ev_path))
                self.assertEqual(repo, "org/repo-from-event")
                self.assertEqual(pr, 123)

    def test_detect_pr_context_from_issue_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ev_path = Path(tmpdir) / "event.json"
            ev_data = {
                "issue": {"number": 234, "pull_request": {}},
                "repository": {"full_name": "org/issue-pr"},
            }
            ev_path.write_text(json.dumps(ev_data), encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                repo, pr = detect_pr_context(event_path=str(ev_path))
                self.assertEqual(repo, "org/issue-pr")
                self.assertEqual(pr, 234)

    def test_detect_pr_context_from_github_ref(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "org/repo", "GITHUB_REF": "refs/pull/77/merge"}, clear=True):
            repo, pr = detect_pr_context()
            self.assertEqual(repo, "org/repo")
            self.assertEqual(pr, 77)

        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "org/repo", "GITHUB_REF": "refs/heads/main"}, clear=True):
            repo, pr = detect_pr_context()
            self.assertEqual(repo, "org/repo")
            self.assertIsNone(pr)


class TestMarkdownRendering(unittest.TestCase):
    """Test Step Summary and PR comment markdown rendering."""

    def test_render_clean_report(self):
        report = DriftReport(
            total_findings=0,
            critical_count=0,
            error_count=0,
            warning_count=0,
            has_blocking_errors=False,
            has_baseline=True,
            baseline_symbols_count=150,
            baseline_files_count=10,
        )

        md = render_summary_markdown(report, is_pr_comment=False)
        self.assertIn("## 🛡️ VibeGuard Audit: PASS ✅", md)
        self.assertIn("Gate Status", md)
        self.assertIn("PASS ✅", md)
        self.assertIn("150 symbols across 10 files", md)
        self.assertIn("No architectural drift or invariant violations were reported in this audit scope", md)
        self.assertNotIn(STICKY_COMMENT_MARKER, md)

        # PR comment version contains sticky marker
        pr_md = render_summary_markdown(report, is_pr_comment=True)
        self.assertIn(STICKY_COMMENT_MARKER, pr_md)

    def test_render_blocking_drift_report(self):
        finding1 = DriftFinding(
            category="contract_drift",
            severity="critical",
            file_path="src/api.py",
            line_number=25,
            title="Removed public API function `serve`",
            message="Contract broken",
            remediation="Restore function `serve`",
        )
        finding2 = DriftFinding(
            category="secret_leak",
            severity="error",
            file_path="src/config.py",
            line_number=10,
            title="Hardcoded AWS secret",
            message="Secret token detected",
            remediation="Use environment variable",
        )
        report = DriftReport(
            total_findings=2,
            critical_count=1,
            error_count=1,
            warning_count=0,
            findings=[finding1, finding2],
            has_blocking_errors=True,
            has_baseline=True,
            baseline_symbols_count=80,
            baseline_files_count=5,
        )

        md = render_summary_markdown(report, patch_created=True, is_pr_comment=True)
        self.assertIn("## 🛡️ VibeGuard Audit: FAIL ❌", md)
        self.assertIn("FAIL ❌", md)
        self.assertIn("| Severity | File | Line | Issue | Remedy |", md)
        self.assertIn("🔴 Critical", md)
        self.assertIn("`src/api.py`", md)
        self.assertIn("25", md)
        self.assertIn("❌ Error", md)
        self.assertIn("`src/config.py`", md)
        self.assertIn("Guidance attached (`vibeguard-remediation.patch`)", md)
        self.assertIn("implementation still needs restoration", md)
        self.assertIn("🤖 <b>AI Remediation Guidance</b>", md)
        self.assertIn(STICKY_COMMENT_MARKER, md)

    def test_render_warning_only_report(self):
        finding = DriftFinding(
            category="rule_violation",
            severity="warning",
            file_path="src/utils.py",
            line_number=5,
            title="Unnecessary import",
            message="Unused import found",
            remediation="Remove import",
        )
        report = DriftReport(
            total_findings=1,
            critical_count=0,
            error_count=0,
            warning_count=1,
            findings=[finding],
            has_blocking_errors=False,
            has_baseline=False,
        )

        md = render_summary_markdown(report)
        self.assertIn("## 🛡️ VibeGuard Audit: PASS with warnings ⚠️", md)
        self.assertIn("Unanchored", md)
        self.assertIn("⚠️ Warning", md)


class TestStepSummaryAndOutputs(unittest.TestCase):
    """Test writing to GitHub Actions step summary and output files."""

    def test_write_step_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = Path(tmpdir) / "summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
                self.assertFalse(write_step_summary("Hello", summary_file=None))

            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
                res = write_step_summary("# Test Summary")
                self.assertTrue(res)
                self.assertIn("# Test Summary", summary_file.read_text(encoding="utf-8"))

    def test_write_github_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "outputs.txt"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": ""}):
                self.assertFalse(write_github_outputs({"key": "val"}, output_file=None))

            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(out_file)}):
                res = write_github_outputs({"status": "pass", "count": 0})
                self.assertTrue(res)
                content = out_file.read_text(encoding="utf-8")
                self.assertIn("status=pass\n", content)
                self.assertIn("count=0\n", content)


class TestCIWorkflowConfiguration(unittest.TestCase):
    def test_workflow_uses_local_action_checkout(self):
        workflow = Path(__file__).parents[1] / ".github" / "workflows" / "vibeguard.yml"
        self.assertIn("uses: ./", workflow.read_text(encoding="utf-8"))


class TestPRCommentBot(unittest.TestCase):
    """Test GitHub REST API interactions and sticky PR comment logic."""

    @staticmethod
    def _mock_http_response(data: Any):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        return mock_resp

    @patch("urllib.request.urlopen")
    def test_post_or_update_pr_comment_create(self, mock_urlopen):
        # Case 1: No existing comment -> POST new comment
        mock_get = self._mock_http_response([{"id": 100, "body": "Normal review comment"}])
        mock_post = self._mock_http_response({
            "id": 200,
            "html_url": "https://github.com/owner/repo/pull/1#issuecomment-200",
        })
        mock_urlopen.side_effect = [mock_get, mock_post]

        res = post_or_update_pr_comment(
            body="New Audit Body " + STICKY_COMMENT_MARKER,
            repo="owner/repo",
            pr_number=1,
            token="ghp_dummy_token",
            api_url="https://api.github.com",
        )

        self.assertEqual(res["action"], "created")
        self.assertEqual(res["id"], 200)
        self.assertEqual(mock_urlopen.call_count, 2)

        # Verify POST request payload
        req_post = mock_urlopen.call_args_list[1][0][0]
        self.assertEqual(req_post.get_method(), "POST")
        self.assertEqual(req_post.full_url, "https://api.github.com/repos/owner/repo/issues/1/comments")
        payload = json.loads(req_post.data.decode("utf-8"))
        self.assertIn(STICKY_COMMENT_MARKER, payload["body"])

    @patch("urllib.request.urlopen")
    def test_post_or_update_pr_comment_update(self, mock_urlopen):
        # Case 2: Existing sticky comment -> PATCH comment
        mock_get = self._mock_http_response([
            {"id": 100, "body": "Old Audit Report\n" + STICKY_COMMENT_MARKER,
             "user": {"id": 7, "login": "audit-bot"}},
        ])
        mock_identity = self._mock_http_response({"id": 7, "login": "audit-bot"})
        mock_patch = self._mock_http_response({
            "id": 100,
            "html_url": "https://github.com/owner/repo/pull/1#issuecomment-100",
        })
        mock_urlopen.side_effect = [mock_get, mock_identity, mock_patch]

        res = post_or_update_pr_comment(
            body="Updated Audit Body\n" + STICKY_COMMENT_MARKER,
            repo="owner/repo",
            pr_number=1,
            token="ghp_dummy_token",
            api_url="https://api.github.com",
        )

        self.assertEqual(res["action"], "updated")
        self.assertEqual(res["id"], 100)
        self.assertEqual(mock_urlopen.call_count, 3)

        # Verify PATCH request
        req_patch = mock_urlopen.call_args_list[2][0][0]
        self.assertEqual(req_patch.get_method(), "PATCH")
        self.assertEqual(req_patch.full_url, "https://api.github.com/repos/owner/repo/issues/comments/100")
        payload = json.loads(req_patch.data.decode("utf-8"))
        self.assertIn("Updated Audit Body", payload["body"])

    @patch("urllib.request.urlopen")
    def test_post_or_update_pr_comment_pagination(self, mock_urlopen):
        # Case 3: 100 comments on page 1 without marker, sticky comment on page 2
        page1 = [{"id": i, "body": f"Comment {i}"} for i in range(1, 101)]
        page2 = [{"id": 105, "body": f"Page 2 report\n{STICKY_COMMENT_MARKER}",
                  "user": {"id": 7, "login": "audit-bot"}}]
        mock_get_p1 = self._mock_http_response(page1)
        mock_get_p2 = self._mock_http_response(page2)
        mock_identity = self._mock_http_response({"id": 7, "login": "audit-bot"})
        mock_patch = self._mock_http_response({"id": 105, "html_url": "https://github.com/owner/repo/pull/1#105"})
        mock_urlopen.side_effect = [mock_get_p1, mock_get_p2, mock_identity, mock_patch]

        res = post_or_update_pr_comment(
            body="New Report", repo="owner/repo", pr_number=1, token="ghp_token",
        )
        self.assertEqual(res["action"], "updated")
        self.assertEqual(res["id"], 105)
        self.assertEqual(mock_urlopen.call_count, 4)

    @patch("urllib.request.urlopen")
    def test_sticky_marker_on_someone_elses_comment_does_not_select_it(self, mock_urlopen):
        comments = [{"id": 100, "body": STICKY_COMMENT_MARKER,
                     "user": {"id": 99, "login": "other-user"}}]
        mock_urlopen.side_effect = [
            self._mock_http_response(comments),
            self._mock_http_response({"id": 7, "login": "audit-bot"}),
            self._mock_http_response({"id": 200, "html_url": "https://github.com/o/r/pull/1#200"}),
        ]
        result = post_or_update_pr_comment("Audit", "o/r", 1, "token")
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["id"], 200)

    @patch("urllib.request.urlopen")
    def test_unverifiable_author_creates_comment_without_overwriting(self, mock_urlopen):
        comments = [{"id": 100, "body": STICKY_COMMENT_MARKER,
                     "user": {"id": 7, "login": "audit-bot"}}]
        mock_urlopen.side_effect = [
            self._mock_http_response(comments),
            urllib.error.HTTPError("https://api.github.com/user", 403, "Forbidden", {}, None),
            self._mock_http_response({"id": 200, "html_url": "https://github.com/o/r/pull/1#200"}),
        ]
        result = post_or_update_pr_comment("Audit", "o/r", 1, "token")
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["id"], 200)

    @patch("urllib.request.urlopen")
    def test_post_or_update_pr_comment_ensures_sticky_marker(self, mock_urlopen):
        mock_get = self._mock_http_response([])
        mock_post = self._mock_http_response({"id": 300, "html_url": "http://comment/300"})
        mock_urlopen.side_effect = [mock_get, mock_post]

        res = post_or_update_pr_comment(
            body="Report without marker initially", repo="owner/repo", pr_number=1, token="token",
        )
        self.assertEqual(res["action"], "created")
        req_post = mock_urlopen.call_args_list[1][0][0]
        payload = json.loads(req_post.data.decode("utf-8"))
        self.assertIn(STICKY_COMMENT_MARKER, payload["body"])

    @patch("urllib.request.urlopen")
    def test_github_api_request_token_prefix(self, mock_urlopen):
        mock_resp = self._mock_http_response({"ok": True})
        mock_urlopen.return_value = mock_resp

        github_api_request("https://api.github.com/user", token="Bearer secret_token")
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.headers["Authorization"], "Bearer secret_token")

    @patch("urllib.request.urlopen")
    def test_github_api_request_http_error_detail(self, mock_urlopen):
        from io import BytesIO
        err_fp = BytesIO(b'{"message": "Resource not accessible by integration"}')
        http_err = urllib.error.HTTPError(
            url="https://api.github.com", code=403, msg="Forbidden", hdrs={}, fp=err_fp
        )
        mock_urlopen.side_effect = http_err

        with self.assertRaises(RuntimeError) as cm:
            github_api_request("https://api.github.com/repos/o/r/issues/1/comments", token="tok")
        self.assertIn("HTTP 403", str(cm.exception))
        self.assertIn("Resource not accessible", str(cm.exception))
        self.assertTrue(err_fp.closed)


class TestCIAuditWorkflow(unittest.TestCase):
    """End-to-end tests for `run_ci_audit` and CLI commands."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name)

        # Create basic project structure
        config = GuardConfig(
            project_name="ci-test",
            protected_paths=["*.py"],
            max_file_lines=400,
            fail_on_hallucinated_imports=True,
            fail_on_ghost_declarations=True,
            fail_on_secret_leak=True,
        )
        config.save_to_file(str(self.project_dir / ".vibeguard.json"))

        self.module_file = self.project_dir / "mod.py"
        self.module_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        # Snapshot baseline
        engine = SnapshotEngine(str(self.project_dir), config)
        engine.save_baseline()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_ci_audit_clean_repo(self):
        args = MagicMock()
        args.project = str(self.project_dir)
        args.fail_on_drift = True
        args.comment_pr = False
        args.create_patch = True
        args.token = None
        args.baseline = None
        args.patch_file = None
        args.json = False

        code = run_ci_audit(args)
        self.assertEqual(code, 0)

    def test_run_ci_audit_blocking_drift_fail(self):
        # Mutate code: remove public function `hello` to trigger contract drift
        self.module_file.write_text("# deleted hello\n", encoding="utf-8")

        args = MagicMock()
        args.project = str(self.project_dir)
        args.fail_on_drift = True
        args.comment_pr = False
        args.create_patch = True
        args.token = None
        args.baseline = None
        args.patch_file = str(self.project_dir / "patch.patch")
        args.json = True

        out = StringIO()
        with patch("sys.stdout", out):
            code = run_ci_audit(args)

        self.assertEqual(code, 1)
        self.assertTrue((self.project_dir / "patch.patch").exists())
        self.assertIn('"has_blocking_errors": true', out.getvalue())

    def test_run_ci_audit_blocking_drift_no_fail(self):
        # Mutate code: remove public function `hello`
        self.module_file.write_text("# deleted hello\n", encoding="utf-8")

        args = MagicMock()
        args.project = str(self.project_dir)
        args.fail_on_drift = False  # --no-fail-on-drift
        args.comment_pr = False
        args.create_patch = False
        args.token = None
        args.baseline = None
        args.patch_file = None
        args.json = False

        code = run_ci_audit(args)
        self.assertEqual(code, 0)

    @patch("vibeguard.github_action.post_or_update_pr_comment")
    def test_run_ci_audit_pr_commenting(self, mock_comment):
        mock_comment.return_value = {"action": "created", "url": "http://comment"}

        args = MagicMock()
        args.project = str(self.project_dir)
        args.fail_on_drift = True
        args.comment_pr = True
        args.create_patch = False
        args.token = "token123"
        args.baseline = None
        args.patch_file = None
        args.pr = 55
        args.repo = "owner/my-repo"
        args.json = False

        code = run_ci_audit(args)
        self.assertEqual(code, 0)
        self.assertTrue(mock_comment.called)
        self.assertEqual(mock_comment.call_args[1]["repo"], "owner/my-repo")
        self.assertEqual(mock_comment.call_args[1]["pr_number"], 55)

    def test_cli_ci_and_action_help(self):
        for cmd in ("ci", "action"):
            out = StringIO()
            with patch("sys.stdout", out):
                with self.assertRaises(SystemExit) as cm:
                    main([cmd, "--help"])
                self.assertEqual(cm.exception.code, 0)
            self.assertIn("--fail-on-drift", out.getvalue())
            self.assertIn("--comment-pr", out.getvalue())
            self.assertIn("--create-patch", out.getvalue())

    def test_cli_ci_bool_flag_normalization(self):
        # Verify --fail-on-drift false and --fail-on-drift=false are normalized and don't crash
        self.module_file.write_text("# delete hello\n", encoding="utf-8")

        # 1. Flag with space: "false"
        code1 = main(["ci", "--project", str(self.project_dir), "--fail-on-drift", "false", "--no-comment-pr", "--no-create-patch"])
        self.assertEqual(code1, 0)

        # 2. Flag with equals: "=false"
        code2 = main(["ci", str(self.project_dir), "--fail-on-drift=false", "--comment-pr=false", "--create-patch=false"])
        self.assertEqual(code2, 0)

        # 3. String boolean in run_ci_audit object
        args = MagicMock()
        args.project = str(self.project_dir)
        args.fail_on_drift = "false"
        args.comment_pr = "no"
        args.create_patch = "0"
        args.token = None
        args.baseline = None
        args.patch_file = None
        args.json = False
        code3 = run_ci_audit(args)
        self.assertEqual(code3, 0)


if __name__ == "__main__":
    unittest.main()
