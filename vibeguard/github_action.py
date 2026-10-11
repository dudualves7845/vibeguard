"""Official GitHub Action & CI Runner for VibeGuard Pull Requests.

Zero-dependency integration for GitHub Actions:
- Renders rich GitHub Step Summary ($GITHUB_STEP_SUMMARY).
- Emits GitHub workflow annotations (::error, ::warning).
- Posts/updates single sticky PR comment (<!-- vibeguard-pr-comment -->).
- Outputs CI artifacts and remediation patches.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

from .drift import DriftEngine, DriftFinding, DriftReport
from .remediation import RemediationGenerator
from .snapshot import ProjectSnapshot

STICKY_COMMENT_MARKER = "<!-- vibeguard-pr-comment -->"
DEFAULT_BASELINE_FILE = ".vibeguard/baseline.json"
DEFAULT_PATCH_FILE = "vibeguard-remediation.patch"
GITHUB_API_DEFAULT = "https://api.github.com"


def str_to_bool(val: Any, default: bool = True) -> bool:
    """Convert string or boolean value to boolean with default fallback."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes", "y", "on")


def detect_pr_context(
    event_path: Optional[str] = None,
    repo_override: Optional[str] = None,
    pr_override: Optional[int] = None,
) -> Tuple[Optional[str], Optional[int]]:
    """Detects GitHub repository name and PR number from environment or event payload."""
    repo = repo_override or os.environ.get("GITHUB_REPOSITORY")
    pr_num = pr_override
    if pr_num is None:
        for var in ("PR_NUMBER", "GITHUB_PR_NUMBER"):
            val = os.environ.get(var)
            if val and val.isdigit():
                pr_num = int(val)
                break

    if pr_num is None:
        ref = os.environ.get("GITHUB_REF", "")
        if ref.startswith("refs/pull/"):
            parts = ref.split("/")
            if len(parts) >= 3 and parts[2].isdigit():
                pr_num = int(parts[2])

    ev_path = event_path or os.environ.get("GITHUB_EVENT_PATH")
    if ev_path and Path(ev_path).is_file():
        try:
            with open(ev_path, "r", encoding="utf-8") as f:
                ev_data = json.load(f)
            if not repo:
                repo = ev_data.get("repository", {}).get("full_name")
            if pr_num is None:
                if "pull_request" in ev_data:
                    pr_num = ev_data["pull_request"].get("number")
                elif "issue" in ev_data and "pull_request" in ev_data["issue"]:
                    pr_num = ev_data["issue"].get("number")
                elif "number" in ev_data and ("pull_request" in ev_data or "issue" in ev_data):
                    pr_num = ev_data.get("number")
        except Exception:
            pass

    return repo, int(pr_num) if pr_num is not None else None


def escape_markdown_cell(text: Any) -> str:
    """Sanitizes text for safe rendering inside GitHub markdown tables."""
    return str(text or "").replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def render_summary_markdown(
    report: DriftReport,
    baseline_path: Optional[str] = None,
    patch_created: bool = False,
    is_pr_comment: bool = False,
) -> str:
    """Renders formatted GitHub Step Summary or PR comment markdown report."""
    blocking = report.has_blocking_errors
    total = report.total_findings

    if blocking:
        status_title = "## 🛡️ VibeGuard Audit: FAIL ❌"
        status_callout = "> 🚨 **Blocking architectural drift detected!** Code changes break protected contracts or invariants."
    elif total > 0:
        status_title = "## 🛡️ VibeGuard Audit: PASS with warnings ⚠️"
        status_callout = "> ⚠️ **No blocking findings; non-blocking warnings remain.** Review recommendations before merging."
    else:
        status_title = "## 🛡️ VibeGuard Audit: PASS ✅"
        status_callout = "> ✨ **No findings were reported in this audit scope.** Review coverage and parser limits before relying on the result."

    base_status = (
        f"Anchored (`{baseline_path or DEFAULT_BASELINE_FILE}`, {report.baseline_symbols_count} symbols across {report.baseline_files_count} files)"
        if report.has_baseline
        else "Unanchored (No baseline snapshot found — intrinsic invariants only)"
    )
    patch_status = "Guidance attached (`vibeguard-remediation.patch`); implementation still needs restoration" if patch_created else ("Guidance available" if total > 0 else "None needed")

    lines = [
        status_title, "", status_callout, "",
        "### 🔒 Protected Contracts Summary", "",
        "| Metric | Status / Value |",
        "| :--- | :--- |",
        f"| **Gate Status** | **{'FAIL ❌' if blocking else 'PASS ✅'}** |",
        f"| **Baseline State** | {base_status} |",
        f"| **Total Findings** | `{report.total_findings}` |",
        f"| **Blocking Issues** | `{report.critical_count + report.error_count}` (Critical: {report.critical_count}, Errors: {report.error_count}) |",
        f"| **Warnings** | `{report.warning_count}` |",
        f"| **Remediation Patch** | {patch_status} |", "",
        "### 📋 Findings Details", "",
    ]

    if not report.findings:
        lines.append("🎉 *No architectural drift or invariant violations were reported in this audit scope.*")
    else:
        lines.extend(["| Severity | File | Line | Issue | Remedy |", "| :--- | :--- | :---: | :--- | :--- |"])
        icon_map = {"critical": "🔴 Critical", "error": "❌ Error", "warning": "⚠️ Warning", "info": "ℹ️ Info"}
        for f in report.findings:
            sev = icon_map.get(f.severity.lower(), f.severity.upper())
            line_str = str(f.line_number) if f.line_number else "-"
            issue_text = f"{f.title} — {f.message}" if f.message and f.message != f.title else f.title
            lines.append(f"| {sev} | `{f.file_path}` | {line_str} | {escape_markdown_cell(issue_text)} | {escape_markdown_cell(f.remediation)} |")

        ai_prompt = RemediationGenerator.generate_ai_prompt(report)
        lines.extend([
            "", "<details>",
            "<summary>🤖 <b>AI Remediation Guidance</b> (Click to expand prompt for Claude / Cursor / Copilot)</summary>",
            "", "```markdown", ai_prompt, "```", "</details>",
        ])

    if is_pr_comment:
        lines.extend(["", "---", "*Automated by [VibeGuard CI](https://github.com/vibe-guard/vibeguard) • Guarding against AI vibe drift*", "", STICKY_COMMENT_MARKER])
    return "\n".join(lines)


def write_step_summary(markdown: str, summary_file: Optional[str] = None) -> bool:
    """Appends markdown report to GitHub Step Summary file."""
    path = summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")
        return True
    except Exception as e:
        sys.stderr.write(f"Failed to write GITHUB_STEP_SUMMARY: {e}\n")
        return False


def write_github_outputs(outputs: Dict[str, Any], output_file: Optional[str] = None) -> bool:
    """Appends key-value pairs to GitHub Actions output environment file."""
    path = output_file or os.environ.get("GITHUB_OUTPUT")
    if not path:
        return False
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for k, v in outputs.items():
                f.write(f"{k}={v}\n")
        return True
    except Exception as e:
        sys.stderr.write(f"Failed to write GITHUB_OUTPUT: {e}\n")
        return False


def github_api_request(
    url: str,
    token: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> Any:
    """Performs HTTP request to GitHub REST API using urllib.request."""
    auth_header = token if token.startswith(("Bearer ", "token ")) else f"Bearer {token}"
    headers = {
        "Authorization": auth_header,
        "Accept": "application/vnd.github+json",
        "User-Agent": "VibeGuard-CI",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data_bytes = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as err:
        err_msg = ""
        try:
            err_msg = err.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        finally:
            err.close()
        detail = f": {err_msg}" if err_msg else ""
        raise RuntimeError(f"GitHub API {method} {url} returned HTTP {err.code} ({err.reason}){detail}") from err


def post_or_update_pr_comment(
    body: str,
    repo: str,
    pr_number: int,
    token: str,
    api_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Posts or updates sticky PR comment to prevent spamming PR conversations."""
    if STICKY_COMMENT_MARKER not in body:
        body = f"{body}\n\n{STICKY_COMMENT_MARKER}"

    base = (api_url or os.environ.get("GITHUB_API_URL") or GITHUB_API_DEFAULT).rstrip("/")
    existing_id = None
    author_id = None
    identity_checked = False

    # Paginate through PR comments up to 1000 comments (10 pages)
    page = 1
    while page <= 10:
        comments_url = f"{base}/repos/{repo}/issues/{pr_number}/comments?per_page=100&page={page}"
        batch = github_api_request(comments_url, token=token, method="GET")
        if not isinstance(batch, list) or not batch:
            break
        for c in batch:
            if STICKY_COMMENT_MARKER in (c.get("body") or ""):
                comment_author = c.get("user") or {}
                if not isinstance(comment_author, dict) or comment_author.get("id") is None:
                    continue
                if not identity_checked:
                    identity_checked = True
                    try:
                        identity = github_api_request(f"{base}/user", token=token, method="GET")
                        if isinstance(identity, dict):
                            author_id = identity.get("id")
                    except Exception:
                        # Integration tokens may not expose a /user identity.
                        # Creating a report is safer than editing an unknown author.
                        author_id = None
                if author_id is not None and comment_author.get("id") == author_id:
                    existing_id = c.get("id")
                    break
        if existing_id is not None or len(batch) < 100:
            break
        page += 1

    if existing_id:
        update_url = f"{base}/repos/{repo}/issues/comments/{existing_id}"
        resp = github_api_request(update_url, token=token, method="PATCH", payload={"body": body})
        return {"action": "updated", "id": existing_id, "url": resp.get("html_url")}
    create_url = f"{base}/repos/{repo}/issues/{pr_number}/comments"
    resp = github_api_request(create_url, token=token, method="POST", payload={"body": body})
    return {"action": "created", "id": resp.get("id"), "url": resp.get("html_url")}


def _load_ci_baseline(project_root: Path, baseline_arg: Optional[str], engine: DriftEngine) -> Optional[ProjectSnapshot]:
    """Loads baseline snapshot from configured path or fallback engine."""
    if baseline_arg:
        bp = Path(baseline_arg)
        if not bp.is_absolute():
            bp = project_root / bp
        if bp.exists():
            try:
                with open(bp, "r", encoding="utf-8") as f:
                    return ProjectSnapshot.from_dict(json.load(f))
            except Exception as e:
                sys.stderr.write(f"Warning: Failed to load baseline from {bp}: {e}\n")
        else:
            sys.stderr.write(f"Warning: Configured baseline file not found at {bp}\n")
    return engine.snapshot_engine.load_baseline()


def _generate_patch_file(
    report: DriftReport, project_root: Path, baseline: Any, create_patch: bool, patch_file: str
) -> bool:
    """Generates remediation patch file if drift is detected."""
    if not create_patch or report.total_findings == 0:
        return False
    patch_text = RemediationGenerator.generate_git_patch(report, str(project_root), baseline)
    if not patch_text:
        return False
    p_out = Path(patch_file)
    if not p_out.is_absolute():
        p_out = project_root / p_out
    try:
        p_out.write_text(patch_text, encoding="utf-8")
        return True
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to write remediation patch: {e}\n")
        return False


def _dispatch_pr_comment(
    report: DriftReport, baseline_path: str, patch_created: bool, repo: Optional[str], pr_num: Optional[int], token: Optional[str]
) -> None:
    """Dispatches sticky PR comment if running in PR context with valid token."""
    if not repo or not pr_num:
        print("ℹ️ PR comment skipped: Not in a Pull Request context.")
        return
    if not token:
        print("ℹ️ PR comment skipped: No GitHub token provided.")
        return
    pr_md = render_summary_markdown(report, baseline_path=baseline_path, patch_created=patch_created, is_pr_comment=True)
    try:
        res = post_or_update_pr_comment(pr_md, repo=repo, pr_number=pr_num, token=token)
        print(f"💬 VibeGuard PR comment {res.get('action')}: {res.get('url', f'#{pr_num}')}")
    except Exception as e:
        sys.stderr.write(f"⚠️ Failed to post PR comment: {e}\n")


def run_ci_audit(args: Any = None) -> int:
    """Executes full CI audit workflow for GitHub Actions or CLI."""
    target_project = getattr(args, "project", None) or getattr(args, "project_opt", None)
    project_root = Path(target_project).resolve() if target_project else Path.cwd()

    fail_flag = getattr(args, "fail_on_drift", None)
    fail_on_drift = str_to_bool(fail_flag, default=True) if fail_flag is not None else str_to_bool(os.environ.get("INPUT_FAIL_ON_DRIFT"), default=True)
    comment_flag = getattr(args, "comment_pr", None)
    comment_pr = str_to_bool(comment_flag, default=True) if comment_flag is not None else str_to_bool(os.environ.get("INPUT_COMMENT_PR"), default=True)
    patch_flag = getattr(args, "create_patch", None)
    create_patch = str_to_bool(patch_flag, default=True) if patch_flag is not None else str_to_bool(os.environ.get("INPUT_CREATE_PATCH"), default=True)

    token = getattr(args, "token", None) or os.environ.get("INPUT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    baseline_arg = getattr(args, "baseline", None) or os.environ.get("INPUT_BASELINE_FILE")
    patch_file_arg = getattr(args, "patch_file", None) or DEFAULT_PATCH_FILE
    base_file_label = str(baseline_arg or DEFAULT_BASELINE_FILE)

    engine = DriftEngine(str(project_root))
    baseline = _load_ci_baseline(project_root, baseline_arg, engine)
    report = engine.evaluate(baseline=baseline)

    annotations = RemediationGenerator.export_github_annotations(report)
    if annotations:
        print(annotations)

    patch_created = _generate_patch_file(report, project_root, baseline, create_patch, patch_file_arg)

    summary_md = render_summary_markdown(report, baseline_path=base_file_label, patch_created=patch_created, is_pr_comment=False)
    write_step_summary(summary_md)

    blocking_count = report.critical_count + report.error_count
    write_github_outputs({
        "status": "fail" if report.has_blocking_errors else "pass",
        "findings-count": report.total_findings,
        "blocking-errors": blocking_count,
        "patch-created": "true" if patch_created else "false",
    })

    if comment_pr:
        repo, pr_num = detect_pr_context(repo_override=getattr(args, "repo", None), pr_override=getattr(args, "pr", None))
        _dispatch_pr_comment(report, base_file_label, patch_created, repo, pr_num, token)

    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2))

    if report.has_blocking_errors:
        print(f"❌ [VibeGuard CI] Audit failed with {blocking_count} blocking issue(s).")
        return 1 if fail_on_drift else 0
    if report.total_findings > 0:
        print(f"⚠️ [VibeGuard CI] Audit passed with {report.warning_count} non-blocking warning(s).")
        return 0
    print("✨ [VibeGuard CI] No drift findings reported in the selected audit scope.")
    return 0


def normalize_ci_bool_flags(args_list: List[str]) -> List[str]:
    """Normalizes --flag=true/false and --flag true/false styles for argparse."""
    res, i, flags = [], 0, {"--fail-on-drift", "--comment-pr", "--create-patch"}
    while i < len(args_list):
        arg = args_list[i]
        eq = next((f for f in flags if arg.lower().startswith(f"{f}=")), None)
        if eq:
            val = arg[len(eq) + 1:].lower()
            res.append(eq if val in ("true", "1", "yes", "y", "on") else f"--no-{eq[2:]}")
        elif arg in flags and i + 1 < len(args_list) and args_list[i + 1].lower() in ("true", "false", "1", "0", "yes", "no"):
            res.append(arg if args_list[i + 1].lower() in ("true", "1", "yes", "y", "on") else f"--no-{arg[2:]}")
            i += 1
        else:
            res.append(arg)
        i += 1
    return res

