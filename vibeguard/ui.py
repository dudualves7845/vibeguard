"""VibeGuard Dashboard service layer: provides unified logic for web UI."""

from datetime import datetime
from functools import wraps
import json
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from .analyzer import CodeAnalyzer
from .config import GuardConfig
from .dependency import DependencyChecker, PYTHON_STDLIB, NODE_BUILTINS, GO_STDLIB, RUST_STDLIB
from .drift import DriftEngine, DriftReport
from .git_utils import GitIntegration
from .remediation import RemediationGenerator
from .snapshot import SnapshotEngine, ProjectSnapshot
from .evaluation import evaluate_project


def project_operation(method):
    """Keep all project-dependent reads/writes within one selection."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class VibeGuardService:
    """Service layer backing the VibeGuard Web Dashboard."""

    def __init__(self, project_root: Optional[str] = None):
        self._lock = threading.RLock()
        init_path = Path(str(project_root).strip().strip("'\"") if project_root else os.getcwd()).expanduser()
        self.project_root = init_path.resolve()
        self._init_project()

    def _init_project(self) -> None:
        """Initializes engines for the current project root."""
        self.config_path = self.project_root / ".vibeguard.json"
        if self.config_path.exists():
            self.config = GuardConfig.load_from_file(str(self.config_path))
        else:
            self.config = GuardConfig(project_name=self.project_root.name)
        self.snapshot_engine = SnapshotEngine(str(self.project_root), self.config)
        self.git_helper = GitIntegration(str(self.project_root))
        self.dep_checker = DependencyChecker(str(self.project_root))
        self.drift_engine = DriftEngine(str(self.project_root), self.config)
        self.last_report: Optional[DriftReport] = None

    @project_operation
    def select_project(self, new_path: str) -> Dict[str, Any]:
        """Switches active project to a new directory path (supports ~ and relative paths)."""
        clean_path = str(new_path).strip().strip("'\"")
        if not clean_path:
            raise ValueError("Project path cannot be empty.")
        p = Path(clean_path).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            raise ValueError(f"Path does not exist or is not a directory: {new_path}")
        GuardConfig.load_from_file(str(p / ".vibeguard.json"))
        with self._lock:
            self.project_root = p
            self._init_project()
            return self.get_project_info()

    @project_operation
    def get_project_info(self) -> Dict[str, Any]:
        """Returns metadata about the active project, baseline, and git hook."""
        has_config = self.config_path.exists()
        baseline = self.snapshot_engine.load_baseline()
        has_baseline = baseline is not None
        baseline_info = None
        if baseline:
            dt = datetime.fromtimestamp(baseline.timestamp) if baseline.timestamp else None
            baseline_info = {
                "timestamp": baseline.timestamp,
                "formatted_date": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "Unknown",
                "total_files": baseline.total_files,
            }

        is_git = self.git_helper.is_git_repo()
        hook_installed = self.git_helper.is_hook_installed() if is_git else False

        try:
            scanned = self.snapshot_engine.scan_files()
            total_files = len(scanned)
        except Exception:
            total_files = baseline.total_files if baseline else 0

        total_symbols = 0
        if baseline and baseline.files:
            for f_info in baseline.files.values():
                total_symbols += len(f_info.get("symbols", []))

        return {
            "project_name": self.config.project_name or self.project_root.name,
            "project_path": str(self.project_root),
            "is_git_repo": is_git,
            "git_root": str(self.git_helper.get_git_root()) if is_git else str(self.project_root),
            "git_hook_installed": hook_installed,
            "has_config": has_config,
            "config": self.config.to_dict(),
            "has_baseline": has_baseline,
            "baseline": baseline_info,
            "total_files": total_files,
            "total_symbols": total_symbols,
            "last_check_status": "clean" if (self.last_report and self.last_report.total_findings == 0) else ("drifted" if self.last_report else "unknown"),
        }

    @project_operation
    def run_check(
        self,
        staged: bool = False,
        diff: bool = False,
        patch: bool = False,
        sarif: bool = False,
    ) -> Dict[str, Any]:
        """Runs invariant and drift evaluation against baseline/working tree."""
        report, baseline = evaluate_project(self.project_root, staged=staged, diff=diff)
        self.last_report = report

        prompt_str = RemediationGenerator.generate_ai_prompt(report)
        summary_str = RemediationGenerator.generate_summary(report)
        patch_str = RemediationGenerator.generate_git_patch(report, str(self.project_root), baseline) if patch else None
        sarif_data = RemediationGenerator.export_sarif(report) if sarif else None

        return {
            "report": report.to_dict(),
            "summary": summary_str,
            "prompt": prompt_str,
            "has_blocking_errors": report.has_blocking_errors,
            "patch": patch_str,
            "sarif": sarif_data,
        }

    @project_operation
    def update_baseline(self) -> Dict[str, Any]:
        """Captures and stores baseline snapshot for current project."""
        snapshot = self.snapshot_engine.create_snapshot()
        baseline_file = self.snapshot_engine.save_baseline(snapshot)
        self.last_report = None
        return {
            "success": True,
            "message": f"Baseline snapshot updated ({snapshot.total_files} files captured).",
            "total_files": snapshot.total_files,
            "timestamp": snapshot.timestamp,
            "baseline_path": str(baseline_file),
        }

    @project_operation
    def manage_hook(self, action: str = "install") -> Dict[str, Any]:
        """Installs or uninstalls git pre-commit hook."""
        if not self.git_helper.is_git_repo():
            raise RuntimeError("Project is not inside a git repository.")
        if action == "uninstall":
            removed = self.git_helper.uninstall_pre_commit_hook()
            return {
                "success": True,
                "installed": False,
                "message": "VibeGuard pre-commit hook removed successfully." if removed else "No active hook found.",
            }
        else:
            hook_path = self.git_helper.install_pre_commit_hook()
            return {
                "success": True,
                "installed": True,
                "message": f"VibeGuard pre-commit hook installed at {hook_path}",
            }

    @project_operation
    def get_prompt(self) -> Dict[str, Any]:
        """Retrieves formatted AI remediation guidance."""
        self.run_check()
        report = self.last_report
        summary = RemediationGenerator.generate_summary(report)
        if not report.has_baseline:
            summary += " | Unanchored: no baseline snapshot found"
        return {
            "prompt": RemediationGenerator.generate_ai_prompt(report),
            "summary": summary,
            "has_blocking_errors": report.has_blocking_errors,
            "total_findings": report.total_findings,
            "has_baseline": report.has_baseline,
        }

    @project_operation
    def generate_patch(self) -> Dict[str, Any]:
        """Generates git unified diff patch restoring missing contracts."""
        baseline = self.snapshot_engine.load_baseline()
        self.run_check()
        patch = RemediationGenerator.generate_git_patch(self.last_report, str(self.project_root), baseline)
        return {
            "patch": patch or "",
            "has_patch": bool(patch and patch.strip()),
            "total_findings": self.last_report.total_findings,
        }

    @project_operation
    def get_symbols(self) -> Dict[str, Any]:
        """Retrieves symbol hierarchy, parameter contracts, and metrics."""
        snapshot = self.snapshot_engine.load_baseline()
        if snapshot is None or not snapshot.files:
            snapshot = self.snapshot_engine.create_snapshot()

        total_symbols = 0
        by_kind: Dict[str, int] = {}
        files_data = []

        for rel_path, f_data in sorted(snapshot.files.items()):
            syms = f_data.get("symbols", [])
            total_symbols += len(syms)
            for s in syms:
                k = s.get("kind", "unknown")
                by_kind[k] = by_kind.get(k, 0) + 1

            files_data.append({
                "path": rel_path,
                "language": f_data.get("language", "unknown"),
                "total_lines": f_data.get("total_lines", 0),
                "symbols": syms,
                "imports": f_data.get("imports", []),
            })

        return {
            "total_symbols": total_symbols,
            "total_files": len(files_data),
            "by_kind": by_kind,
            "files": files_data,
        }

    @project_operation
    def get_dependencies(self) -> Dict[str, Any]:
        """Audits project imports and categorizes verified vs hallucinated."""
        snapshot = self.snapshot_engine.create_snapshot()
        self.dep_checker = DependencyChecker(str(self.project_root))

        local_packages: Set[str] = set()
        for p in self.project_root.iterdir():
            if p.is_dir() and not p.name.startswith((".", "_")):
                local_packages.add(p.name)
            elif p.is_file() and p.suffix in (".py", ".js", ".ts", ".go", ".rs", ".java"):
                local_packages.add(p.stem)

        dep_map: Dict[str, Dict[str, Any]] = {}
        for rel_path, f_data in snapshot.files.items():
            lang = f_data.get("language", "").lower()
            imports = f_data.get("imports", [])
            for imp in imports:
                key = f"{lang}:{imp}"
                if key not in dep_map:
                    status, reason, category = self._verify_single_import(imp, lang, local_packages)
                    dep_map[key] = {
                        "name": imp,
                        "language": lang,
                        "status": status,
                        "category": category,
                        "reason": reason,
                        "files": [rel_path],
                    }
                else:
                    if rel_path not in dep_map[key]["files"]:
                        dep_map[key]["files"].append(rel_path)

        dep_list = list(dep_map.values())
        verified = [d for d in dep_list if d["status"] == "verified"]
        hallucinated = [d for d in dep_list if d["status"] == "hallucinated"]

        return {
            "total_imports": len(dep_list),
            "verified_count": len(verified),
            "hallucinated_count": len(hallucinated),
            "dependencies": dep_list,
            "hallucinated": hallucinated,
            "verified": verified,
        }

    def _verify_single_import(self, imp: str, lang: str, local_packages: Set[str]) -> Tuple[str, str, str]:
        """Checks if a given import is verified or hallucinated."""
        if not imp:
            return "verified", "Empty import", "system"
        if lang == "python":
            root_mod = imp.split(".")[0].strip()
            if root_mod in PYTHON_STDLIB:
                return "verified", "Python standard library", "stdlib"
            if root_mod in local_packages or (self.project_root / f"{root_mod}.py").exists() or (self.project_root / root_mod).is_dir():
                return "verified", "Local project module", "local"
            if self.dep_checker.check_python_import(imp, local_packages):
                return "verified", "Declared in dependencies or environment", "installed"
            return "hallucinated", f"Module '{root_mod}' not found in stdlib, virtualenv, or manifests", "unresolved"
        elif lang in ("javascript", "typescript"):
            if imp.startswith((".", "/", "@/", "~/")):
                return "verified", "Local relative module", "local"
            if imp in NODE_BUILTINS or imp.startswith("node:"):
                return "verified", "Node.js standard builtin", "stdlib"
            pkg = imp.split("/")[0] if not imp.startswith("@") else "/".join(imp.split("/")[:2])
            if self.dep_checker.check_js_import(imp, local_packages):
                return "verified", "Declared in package.json or local module", "manifest"
            return "hallucinated", f"Package '{pkg}' not found in package.json dependencies", "unresolved"
        elif lang == "go":
            if imp in GO_STDLIB:
                return "verified", "Go standard library", "stdlib"
            if self.dep_checker.check_go_import(imp, local_packages):
                return "verified", "Go module or local package", "manifest"
            return "hallucinated", f"Go package '{imp}' not declared in go.mod or stdlib", "unresolved"
        elif lang == "rust":
            if imp in RUST_STDLIB or imp in ("crate", "super", "self"):
                return "verified", "Rust standard library or keyword", "stdlib"
            if self.dep_checker.check_rust_import(imp, local_packages):
                return "verified", "Declared in Cargo.toml", "manifest"
            return "hallucinated", f"Crate '{imp}' not found in Cargo.toml dependencies", "unresolved"
        return "verified", f"Language {lang} dependency", "external"

    @project_operation
    def update_config(self, new_config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Saves updated configuration to .vibeguard.json."""
        with self._lock:
            merged_data = self.config.to_dict()
            merged_data.update(new_config_dict)
            updated_config = GuardConfig.from_dict(merged_data)
            updated_config.save_to_file(str(self.config_path))
            self.config = updated_config
            self.snapshot_engine.config = updated_config
            self.drift_engine.config = updated_config
            if hasattr(self.drift_engine, "snapshot_engine"):
                self.drift_engine.snapshot_engine.config = updated_config
            self.last_report = None
            return {
                "success": True,
                "message": "Configuration saved to .vibeguard.json",
                "config": self.config.to_dict(),
            }

    @project_operation
    def get_badge(self) -> Dict[str, Any]:
        """Returns badge data (status, color, SVG, snippets) for active project."""
        from .badge import (
            BadgeGenerator,
            BadgeState,
            STATE_ERROR,
            LABEL_VIBEGUARD,
            MSG_ERROR,
            COLOR_ERROR,
            get_state_from_report,
            DEFAULT_LINK_URL,
            DEFAULT_FILENAME,
        )
        has_baseline = (self.project_root / ".vibeguard/baseline.json").exists()
        report, audit_error = None, None
        try:
            report, baseline = evaluate_project(self.project_root)
            self.last_report = report
            has_baseline = baseline is not None
        except (ValueError, OSError, RuntimeError) as exc:
            audit_error = str(exc)

        state = (BadgeState(STATE_ERROR, LABEL_VIBEGUARD, MSG_ERROR, COLOR_ERROR,
                           has_baseline, 0, audit_error) if audit_error else
                 get_state_from_report(report=report, has_baseline=has_baseline))
        svg = BadgeGenerator.generate_badge_for_state(state)
        out_path = self.project_root / DEFAULT_FILENAME
        try:
            BadgeGenerator.save_badge(out_path, svg)
        except OSError:
            pass
        markdown = BadgeGenerator.generate_markdown_snippet(
            image_path=DEFAULT_FILENAME,
            link_url=DEFAULT_LINK_URL,
        )
        html = BadgeGenerator.generate_html_snippet(
            image_path=DEFAULT_FILENAME,
            link_url=DEFAULT_LINK_URL,
        )
        return {
            **state.to_dict(),
            "svg": svg,
            "output_path": str(out_path),
            "markdown": markdown,
            "html": html,
            "error": audit_error,
        }

    @project_operation
    def get_checkpoints(self) -> List[Dict[str, Any]]:
        from .rewind import CheckpointManager
        return [c.to_dict() for c in CheckpointManager(str(self.project_root)).list_checkpoints()]

    @project_operation
    def create_checkpoint(self, message: Optional[str] = None) -> Dict[str, Any]:
        from .rewind import CheckpointManager
        return CheckpointManager(str(self.project_root)).create_checkpoint(message=message).to_dict()

    @project_operation
    def rewind(self, checkpoint_id: Optional[str] = None, step: int = 1) -> Dict[str, Any]:
        from .rewind import CheckpointManager
        mgr = CheckpointManager(str(self.project_root))
        result = mgr.rewind_to(checkpoint_id) if checkpoint_id else mgr.rewind_step(step=step)
        self.last_report = None
        return result

