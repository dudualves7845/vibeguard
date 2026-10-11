"""Command Line Interface for VibeGuard."""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import List, Optional, Set

from .config import GuardConfig, IntentRule
from .drift import DriftEngine, DriftReport
from .git_utils import GitIntegration
from .remediation import RemediationGenerator
from .snapshot import SnapshotEngine
from .watcher import FileWatcher
from .evaluation import evaluate_project

RESET, BOLD, RED, GREEN = "\033[0m", "\033[1m", "\033[31m", "\033[32m"
YELLOW, BLUE, CYAN, GRAY = "\033[33m", "\033[34m", "\033[36m", "\033[90m"


def colorize(text: str, color: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    return f"{color}{text}{RESET}"


def _resolve_root(args: argparse.Namespace) -> Path:
    target = getattr(args, "project", None) or getattr(args, "project_opt", None)
    return Path(target).resolve() if target else Path.cwd()


def cmd_init(args: argparse.Namespace) -> int:
    root = _resolve_root(args)
    config_file = root / ".vibeguard.json"

    if config_file.exists() and not args.force:
        print(colorize(f"⚠️  VibeGuard configuration already exists at {config_file}", YELLOW))
        return 0

    rule = IntentRule(
        id="no-mock-in-production",
        description="Do not import unittest.mock inside production source modules",
        rule_type="forbidden_import", target_patterns=["vibeguard/*.py", "src/*.py"],
        parameters={"forbidden": ["unittest.mock", "mock"]}, severity="warning",
    )
    config = GuardConfig(
        project_name=root.name,
        protected_paths=["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.go", "*.rs", "*.java"],
        max_file_lines=400, fail_on_hallucinated_imports=True,
        fail_on_ghost_declarations=True, fail_on_secret_leak=True, rules=[rule],
    )
    config.save_to_file(str(config_file))
    print(colorize(f"✨ Initialized VibeGuard configuration at {config_file}", GREEN))

    engine = SnapshotEngine(str(root), config)
    baseline_file = engine.save_baseline()
    print(colorize(f"📸 Created initial baseline snapshot at {baseline_file}", GREEN))
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    root = _resolve_root(args)
    engine = SnapshotEngine(str(root))
    snapshot = engine.create_snapshot()
    engine.save_baseline(snapshot)
    print(colorize(f"📸 Baseline snapshot updated ({snapshot.total_files} files captured).", GREEN))
    return 0


def cmd_install_hook(args: argparse.Namespace) -> int:
    git_helper = GitIntegration(str(_resolve_root(args)))
    if not git_helper.is_git_repo():
        print(colorize("❌ Not a git repository. Cannot install pre-commit hook.", RED))
        return 1
    try:
        hook_path = git_helper.install_pre_commit_hook()
        print(colorize(f"🛡️  VibeGuard pre-commit hook successfully installed at {hook_path}\n"
                       f"   Now all 'git commit' runs will automatically guard staged files against drift!", GREEN))
        return 0
    except Exception as e:
        print(colorize(f"❌ Failed to install hook: {e}", RED))
        return 1


def cmd_uninstall_hook(args: argparse.Namespace) -> int:
    git_helper = GitIntegration(str(_resolve_root(args)))
    if git_helper.uninstall_pre_commit_hook():
        print(colorize("✨ VibeGuard pre-commit hook removed successfully.", GREEN))
        return 0
    print(colorize("ℹ️  No active VibeGuard pre-commit hook found.", YELLOW))
    return 0


def _print_report(report: DriftReport, scope_name: str = "") -> None:
    if not report.has_baseline:
        print(colorize("ℹ️  [VibeGuard] Notice: No baseline snapshot found. Contract drift checks are unanchored.\n"
                       "   👉 Run `vibeguard baseline` to lock in API contracts and enable regression tracking.", YELLOW))

    if report.total_findings == 0:
        if report.has_baseline:
            ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(report.baseline_timestamp)) if report.baseline_timestamp else "unknown"
            print(colorize(f"✨ [VibeGuard] All clear! 0 drift findings{scope_name} relative to baseline\n"
                           f"   (captured at {ts_str} with {report.baseline_symbols_count} symbols across {report.baseline_files_count} files).\n"
                           f"   No findings in the selected audit scope; this is not a formal proof of correctness.", GREEN))
        else:
            print(colorize(f"✨ [VibeGuard] All clear! 0 invariant findings{scope_name}.\n"
                           f"   No findings in the selected audit scope; contract checks require a baseline.", GREEN))
        return

    banner = f" {scope_name}" if scope_name else ""
    print(colorize(f"\n🛡️  VIBEGUARD DRIFT AUDIT{banner}: {RemediationGenerator.generate_summary(report)}\n", BOLD))

    for item in report.findings:
        sev_color = RED if item.severity in ("critical", "error") else YELLOW
        sev_tag = colorize(f"[{item.severity.upper()}]", sev_color)
        loc = f"{item.file_path}:{item.line_number}" if item.line_number else item.file_path
        print(f" {sev_tag} {colorize(loc, GRAY)}: {colorize(item.title, BOLD)}\n"
              f"    {colorize('Issue:', GRAY)} {item.message}\n"
              f"    {colorize('Remedy:', CYAN)} {item.remediation}\n")


def cmd_check(args: argparse.Namespace) -> int:
    cwd = _resolve_root(args)
    git_helper = GitIntegration(str(cwd))
    explicit_project = getattr(args, "project", None) or getattr(args, "project_opt", None)
    root = git_helper.get_git_root() if not explicit_project and git_helper.is_git_repo() else cwd
    report, baseline = evaluate_project(root, staged=getattr(args, "staged", False),
                                        diff=getattr(args, "diff", False))

    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2))
        return 1 if report.has_blocking_errors else 0

    if getattr(args, "sarif", False):
        print(json.dumps(RemediationGenerator.export_sarif(report), indent=2))
        return 1 if report.has_blocking_errors else 0

    if getattr(args, "annotations", False):
        print(RemediationGenerator.export_github_annotations(report))
        return 1 if report.has_blocking_errors else 0

    if getattr(args, "patch", False):
        patch = RemediationGenerator.generate_git_patch(report, str(root), baseline)
        print(patch or colorize("ℹ️  No auto-patch could be generated for current findings.", GRAY))
        return 1 if report.has_blocking_errors else 0

    scope = " (staged changes)" if getattr(args, "staged", False) else (" (modified files)" if getattr(args, "diff", False) else "")
    _print_report(report, scope)

    if args.prompt:
        print(colorize("--- AI REMEDIATION PROMPT (Pass this to your AI model) ---", BLUE))
        print(RemediationGenerator.generate_ai_prompt(report))
        print(colorize("---------------------------------------------------------", BLUE))

    if report.has_blocking_errors:
        print(colorize("❌ Drift check failed with blocking issues. Fix before committing or shipping.", RED))
        return 1

    if report.total_findings > 0:
        print(colorize("⚠️  Drift check passed with non-blocking warnings.", YELLOW))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    root = _resolve_root(args)
    poll = getattr(args, "interval", 1.0) or 1.0
    print(colorize(f"👁️  [VibeGuard Watch] Monitoring {root.name} for AI drift (interval: {poll}s)...", BOLD))
    print(colorize("   Press Ctrl+C to stop.", GRAY))

    watcher = FileWatcher(str(root), poll_interval=poll)

    def on_change(report: DriftReport, changed_files: Set[str]) -> None:
        ts = time.strftime("%H:%M:%S")
        files_str = ", ".join(sorted(list(changed_files))[:3])
        if len(changed_files) > 3:
            files_str += f" (+{len(changed_files) - 3} more)"
        print(colorize(f"\n[{ts}] Drift check triggered by: {files_str}", CYAN))
        _print_report(report)
        if report.findings and getattr(args, "prompt", False):
            print(colorize("--- AI REMEDIATION PROMPT ---", BLUE))
            print(RemediationGenerator.generate_ai_prompt(report))
            print(colorize("-----------------------------", BLUE))

    try:
        watcher.run_loop(on_report=on_change)
    except KeyboardInterrupt:
        print(colorize("\n🛑 VibeGuard watch stopped.", YELLOW))
        return 0
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    root = _resolve_root(args)
    drift_engine = DriftEngine(str(root))
    report = drift_engine.evaluate()
    ai_prompt = RemediationGenerator.generate_ai_prompt(report)
    print(ai_prompt)
    return 0 if not report.has_blocking_errors else 1


def cmd_ui(args: argparse.Namespace) -> int:
    from .server import run_server
    target = _resolve_root(args) if (getattr(args, "project", None) or getattr(args, "project_opt", None)) else None
    run_server(host=getattr(args, "host", "127.0.0.1") or "127.0.0.1", port=getattr(args, "port", 8585) or 8585,
               open_browser=not getattr(args, "no_browser", False), project_path=str(target) if target else None)
    return 0



def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp import get_config_snippets, install_antigravity_config, install_cursor_config, run_mcp_server
    if getattr(args, "config_snippet", False):
        print(get_config_snippets())
        return 0
    target = _resolve_root(args) if (getattr(args, "project", None) or getattr(args, "project_opt", None)) else None
    if getattr(args, "install_cursor", False):
        print(colorize(f"✨ Installed Cursor MCP configuration to {install_cursor_config(target_dir=target)}", GREEN))
        return 0
    if getattr(args, "install_antigravity", False):
        print(colorize(f"✨ Installed Antigravity MCP configuration to {install_antigravity_config()}", GREEN))
        return 0
    return run_mcp_server(project_path=str(target) if target else None)


def cmd_badge(args: argparse.Namespace) -> int:
    from .badge import DEFAULT_FILENAME, generate_project_badge_file
    target_dir = _resolve_root(args)
    if not target_dir.exists() or not target_dir.is_dir():
        print(colorize(f"❌ Project directory does not exist: {target_dir}", RED))
        return 1

    out_arg = getattr(args, "output", None)
    out_path = Path(out_arg).resolve() if out_arg and Path(out_arg).is_absolute() else (target_dir / (out_arg or DEFAULT_FILENAME)).resolve()
    try:
        res = generate_project_badge_file(project_root=target_dir, output_path=out_path)
    except Exception as e:
        print(colorize(f"❌ Failed to generate badge: {e}", RED))
        return 1

    for fmt, key in [("json", None), ("markdown", "markdown"), ("html", "html")]:
        if getattr(args, fmt, False):
            print(json.dumps(res, indent=2) if key is None else res[key])
            return 0

    status_str = f"{res['label']} | {res['message']}"
    print(colorize(f"🛡️  [VibeGuard Badge] Generated {colorize(status_str, BOLD)} badge ({res['color']})\n   Saved to: {res['output_path']}\n\n📋 Paste this into your README.md:\n{res['markdown']}", GREEN))
    return 0


def cmd_ci(args: argparse.Namespace) -> int:
    from .github_action import run_ci_audit
    if getattr(args, "project_opt", None):
        args.project = args.project_opt
    return run_ci_audit(args)


def cmd_checkpoint(args: argparse.Namespace) -> int:
    from .rewind import run_checkpoint_cli
    return run_checkpoint_cli(args)


def cmd_rewind(args: argparse.Namespace) -> int:
    from .rewind import run_rewind_cli
    return run_rewind_cli(args)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="vibeguard", description="VibeGuard - The AI Vibe Coding Invariant & Drift Engine")
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    def _add_proj(p: argparse.ArgumentParser) -> None:
        p.add_argument("project", nargs="?", default=None, help="Optional project directory path")
        p.add_argument("--project", dest="project_opt", default=None, help="Target project directory path")

    for name, hlp, fn in [
        ("init", "Initialize VibeGuard in current project", cmd_init),
        ("baseline", "Capture/update baseline snapshot", cmd_baseline),
        ("install-hook", "Install automated git pre-commit hook", cmd_install_hook),
        ("uninstall-hook", "Uninstall automated git pre-commit hook", cmd_uninstall_hook),
        ("prompt", "Directly output AI feedback prompt for the model", cmd_prompt),
    ]:
        sp = subparsers.add_parser(name, help=hlp)
        _add_proj(sp)
        if name == "init":
            sp.add_argument("--force", "-f", action="store_true", help="Overwrite existing configuration")
        sp.set_defaults(func=fn)

    p_check = subparsers.add_parser("check", help="Evaluate codebase against invariants and baseline")
    _add_proj(p_check)
    for flg, hlp in [
        ("--staged", "Only check files staged for git commit"), ("--diff", "Only check files changed relative to git HEAD"),
        ("--prompt", "Print AI-tailored remediation prompt"), ("--json", "Output report in JSON format"),
        ("--sarif", "Output report in SARIF v2.1.0 format"), ("--annotations", "Output GitHub Actions annotations"),
        ("--patch", "Generate automated git patch to restore contracts"),
    ]:
        p_check.add_argument(flg, action="store_true", help=hlp)
    p_check.set_defaults(func=cmd_check)

    p_cp = subparsers.add_parser("checkpoint", help="Capture an AI session checkpoint")
    p_cp.add_argument("message", nargs="*", help="Optional checkpoint description")
    p_cp.add_argument("--project", default=None, help="Target project directory")
    p_cp.set_defaults(func=cmd_checkpoint)

    p_rw = subparsers.add_parser("rewind", help="Safely roll back workspace to a prior checkpoint")
    for flg, opt, hlp in [("--to", None, "Specific checkpoint ID to restore"), ("--diff", None, "Show diff against checkpoint ID"), ("--project", None, "Target project directory")]:
        p_rw.add_argument(flg, default=opt, help=hlp)
    p_rw.add_argument("--step", type=int, default=1, help="Steps back to rewind (default: 1)")
    p_rw.add_argument("--list", action="store_true", help="List session checkpoints history")
    p_rw.add_argument("--keep-untracked", action="store_true", help="Keep untracked files created after checkpoint")
    p_rw.set_defaults(func=cmd_rewind)

    p_cps = subparsers.add_parser("checkpoints", help="Alias for 'rewind --list'")
    p_cps.add_argument("--project", default=None, help="Target project directory")
    p_cps.set_defaults(func=lambda a: cmd_rewind(argparse.Namespace(list=True, project=a.project, to=None, step=1, diff=None, keep_untracked=False)))

    p_watch = subparsers.add_parser("watch", help="Continuously monitor files for real-time drift detection")
    _add_proj(p_watch)
    p_watch.add_argument("--interval", "-i", type=float, default=1.0, help="Polling interval in seconds (default: 1.0)")
    p_watch.add_argument("--prompt", action="store_true", help="Print AI prompt on drift detection")
    p_watch.set_defaults(func=cmd_watch)

    for name in ["ui", "dashboard"]:
        p_web = subparsers.add_parser(name, help="Launch interactive Web Dashboard")
        _add_proj(p_web)
        p_web.add_argument("--port", "-p", type=int, default=8585, help="Port to listen on (default: 8585)")
        p_web.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
        p_web.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
        p_web.set_defaults(func=cmd_ui)

    p_mcp = subparsers.add_parser("mcp", help="Native Model Context Protocol (MCP) stdio server")
    _add_proj(p_mcp)
    for flg, hlp in [
        ("--config-snippet", "Print MCP configuration snippets for Cursor, Claude, Antigravity"),
        ("--install-cursor", "Automatically generate or update .cursor/mcp.json in current project"),
        ("--install-antigravity", "Automatically write MCP config for Antigravity"),
    ]:
        p_mcp.add_argument(flg, action="store_true", help=hlp)
    p_mcp.set_defaults(func=cmd_mcp)

    p_badge = subparsers.add_parser("badge", help="Generate official VibeGuard SVG badge and snippets")
    _add_proj(p_badge)
    p_badge.add_argument("--output", "-o", default=None, help="Output file path (default: vibeguard-badge.svg)")
    for flg, hlp in [("--markdown", "Print Markdown snippet"), ("--html", "Print HTML snippet"), ("--json", "Output JSON")]:
        p_badge.add_argument(flg, action="store_true", help=hlp)
    p_badge.set_defaults(func=cmd_badge)

    for name, hlp in [("ci", "CI invariant & drift audit for pull requests"), ("action", "Alias for 'ci'")]:
        p_ci = subparsers.add_parser(name, help=hlp)
        _add_proj(p_ci)
        for flg, dsc in [("--fail-on-drift", "Exit non-zero on blocking issues"), ("--comment-pr", "Post or update sticky PR comment"), ("--create-patch", "Generate remediation patch")]:
            dest = flg[2:].replace("-", "_")
            group = p_ci.add_mutually_exclusive_group()
            group.add_argument(flg, dest=dest, action="store_true", default=None, help=dsc)
            group.add_argument("--no-" + flg[2:], dest=dest, action="store_false")
        for flg, opt, dsc in [("--token", "-t", "GitHub token"), ("--baseline", "-b", "Baseline snapshot path"), ("--patch-file", "-p", "Patch file path")]:
            p_ci.add_argument(flg, opt, default="vibeguard-remediation.patch" if flg == "--patch-file" else None, help=dsc)
        p_ci.add_argument("--pr", type=int, default=None, help="PR number override")
        p_ci.add_argument("--repo", default=None, help="GitHub repository (owner/repo)")
        p_ci.add_argument("--json", action="store_true", help="Output audit report as JSON")
        p_ci.set_defaults(func=cmd_ci)

    raw_args = list(argv) if argv is not None else sys.argv[1:]
    from .github_action import normalize_ci_bool_flags
    args = parser.parse_args(normalize_ci_bool_flags(raw_args))
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (ValueError, OSError, RuntimeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc), "has_blocking_errors": True,
                              "total_findings": 1, "error_count": 1}))
        else:
            print(f"VibeGuard error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

