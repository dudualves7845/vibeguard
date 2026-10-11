"""Diff and Drift detection engine.

Compares current codebase state against a baseline snapshot or git working tree,
identifying:
- Deleted or mutated public API contracts (functions removed or signatures changed)
- Bloated files exceeding maximum line thresholds
- Injected placeholders (TODO/FIXME/stubs/empty pass statements)
- Hallucinated imports (packages not installed or declared)
- Secret leaks (API keys, tokens)
- Rule/Invariant violations (immutability, cyclomatic complexity, dangerous calls)
"""

from dataclasses import dataclass, field
import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .analyzer import CodeAnalyzer
from .config import GuardConfig, IntentRule
from .dependency import DependencyChecker
from .snapshot import ProjectSnapshot, SnapshotEngine
from .drift_serialization import report_to_dict


@dataclass
class DriftFinding:
    """A detected architectural deviation or quality drift."""
    category: str
    severity: str
    file_path: str
    line_number: Optional[int]
    title: str
    message: str
    remediation: str


@dataclass
class DriftReport:
    """Comprehensive drift analysis report."""
    total_findings: int
    critical_count: int
    error_count: int
    warning_count: int
    findings: List[DriftFinding] = field(default_factory=list)
    has_blocking_errors: bool = False
    has_baseline: bool = False
    baseline_timestamp: Optional[float] = None
    baseline_files_count: int = 0
    baseline_symbols_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return report_to_dict(self)


class DriftEngine:
    """Core evaluation engine that detects unintended changes and degradation."""

    def __init__(self, root_dir: str, config: Optional[GuardConfig] = None):
        self.root_dir = Path(root_dir).resolve()
        self.config = config or GuardConfig.load_from_file(str(self.root_dir / ".vibeguard.json"))
        self.snapshot_engine = SnapshotEngine(str(self.root_dir), self.config)
        self.dep_checker = DependencyChecker(str(self.root_dir))

    def evaluate(
        self,
        baseline: Optional[ProjectSnapshot] = None,
        target_files: Optional[List[str]] = None,
    ) -> DriftReport:
        """Compare current project state with a saved baseline."""
        return self.evaluate_snapshot(None, baseline=baseline, target_files=target_files)

    def evaluate_snapshot(
        self,
        current_snapshot: Optional[ProjectSnapshot] = None,
        baseline: Optional[ProjectSnapshot] = None,
        target_files: Optional[List[str]] = None,
    ) -> DriftReport:
        """Evaluate a supplied current snapshot or scan the project."""
        return self._evaluate_snapshot(current_snapshot, baseline, target_files)

    def _evaluate_snapshot(
        self,
        current_snapshot: Optional[ProjectSnapshot],
        baseline: Optional[ProjectSnapshot],
        target_files: Optional[List[str]],
    ) -> DriftReport:
        """Evaluate an explicitly supplied current snapshot."""
        if baseline is None:
            baseline = self.snapshot_engine.load_baseline()

        file_filter = set(target_files) if target_files is not None else None
        current = current_snapshot if current_snapshot is not None else self.snapshot_engine.create_snapshot(file_filter=file_filter)
        findings: List[DriftFinding] = []

        local_packages = set()
        for p in self.root_dir.iterdir():
            if p.is_dir() and not p.name.startswith((".", "_")):
                local_packages.add(p.name)
            elif p.is_file() and p.suffix in (".py", ".js", ".ts", ".go", ".rs", ".java"):
                local_packages.add(p.stem)

        b_symbols_count = 0
        if baseline is not None:
            self._compare_with_baseline(baseline, current, findings, file_filter)
            for f_info in baseline.files.values():
                b_symbols_count += len(f_info.get("symbols", []))

        self._evaluate_current_intrinsics(current, local_packages, findings)

        critical_count = sum(1 for f in findings if f.severity == "critical")
        error_count = sum(1 for f in findings if f.severity == "error")
        warning_count = sum(1 for f in findings if f.severity == "warning")
        has_blocking = (critical_count > 0) or (error_count > 0)

        return DriftReport(
            total_findings=len(findings),
            critical_count=critical_count,
            error_count=error_count,
            warning_count=warning_count,
            findings=findings,
            has_blocking_errors=has_blocking,
            has_baseline=baseline is not None,
            baseline_timestamp=baseline.timestamp if baseline else None,
            baseline_files_count=baseline.total_files if baseline else 0,
            baseline_symbols_count=b_symbols_count,
        )

    def _compare_with_baseline(
        self,
        baseline: ProjectSnapshot,
        current: ProjectSnapshot,
        findings: List[DriftFinding],
        file_filter: Optional[Set[str]] = None,
    ) -> None:
        for rel_path, b_info in baseline.files.items():
            if file_filter is not None and rel_path not in file_filter:
                continue
            if rel_path not in current.files:
                findings.append(
                    DriftFinding(
                        category="api_breakage",
                        severity="error",
                        file_path=rel_path,
                        line_number=None,
                        title=f"Protected file removed: {rel_path}",
                        message=f"File '{rel_path}' existed in baseline but was deleted by AI generation.",
                        remediation="Restore the missing file or update baseline intentionally with `vibeguard baseline`.",
                    )
                )
                continue

            c_info = current.files[rel_path]
            b_symbols = [s for s in b_info.get("symbols", []) if s.get("is_exported", True)]
            c_symbols: Dict[str, List[Dict[str, Any]]] = {}
            for symbol in c_info.get("symbols", []):
                c_symbols.setdefault(symbol["name"], []).append(symbol)

            for b_sym in b_symbols:
                sym_name = b_sym["name"]
                candidates = c_symbols.get(sym_name, [])
                signature = b_sym.get("signature")
                if signature is not None:
                    candidates = [s for s in candidates if s.get("signature") == signature]
                if not candidates:
                    findings.append(
                        DriftFinding(
                            category="api_breakage",
                            severity="error",
                            file_path=rel_path,
                            line_number=b_sym.get("line_number"),
                            title=f"Public {b_sym['kind']} vanished: {sym_name}{signature or ''}",
                            message=f"The AI removed symbol '{sym_name}' which was part of the existing baseline contract.",
                            remediation=f"Re-implement '{sym_name}' or verify if deprecation is intended.",
                        )
                    )
                else:
                    # Legacy baselines have names/parameters only. Match those
                    # when possible without requiring metadata they never stored.
                    c_sym = next((s for s in candidates if s.get("parameters", []) == b_sym.get("parameters", [])), candidates[0])
                    if not c_sym.get("is_exported", True) or c_sym.get("kind") != b_sym.get("kind"):
                        findings.append(DriftFinding(
                            category="api_breakage", severity="error", file_path=rel_path,
                            line_number=c_sym.get("line_number"),
                            title=f"Public contract changed for '{sym_name}'",
                            message=f"Symbol '{sym_name}' no longer has its public {b_sym.get('kind')} contract.",
                            remediation="Restore the original public symbol kind and visibility.",
                        ))
                    b_params = b_sym.get("parameters", [])
                    c_params = c_sym.get("parameters", [])
                    richer_changed = (b_sym.get("parameter_contract") is not None and b_sym.get("parameter_contract") != c_sym.get("parameter_contract"))
                    richer_changed = richer_changed or ("is_async" in b_sym and b_sym["is_async"] != c_sym.get("is_async", False))
                    richer_changed = richer_changed or ("return_annotation" in b_sym and b_sym["return_annotation"] != c_sym.get("return_annotation"))
                    if b_params != c_params or richer_changed:
                        findings.append(
                            DriftFinding(
                                category="api_breakage",
                                severity="warning",
                                file_path=rel_path,
                                line_number=c_sym.get("line_number"),
                                title=f"Parameter signature changed for '{sym_name}'",
                                message=f"Expected parameters {b_params}, found {c_params}. May break external callers.",
                                remediation="Keep backward compatibility or use default parameters.",
                            )
                        )

    def _evaluate_current_intrinsics(
        self,
        current: ProjectSnapshot,
        local_packages: Set[str],
        findings: List[DriftFinding],
    ) -> None:
        for rel_path, c_info in current.files.items():
            lang = c_info.get("language")
            total_lines = c_info.get("total_lines", 0)

            # 1. File bloat check
            if total_lines > self.config.max_file_lines:
                findings.append(
                    DriftFinding(
                        category="bloat",
                        severity="warning",
                        file_path=rel_path,
                        line_number=total_lines,
                        title=f"File exceeds line threshold ({total_lines} > {self.config.max_file_lines})",
                        message=f"AI generated code expanded file '{rel_path}' to {total_lines} lines.",
                        remediation="Refactor and decompose into smaller, high-cohesion modules (<400 lines).",
                    )
                )

            # 2. Secret leak checks
            for sec in c_info.get("secret_findings", []):
                findings.append(
                    DriftFinding(
                        category="secret_leak",
                        severity="critical" if self.config.fail_on_secret_leak else "warning",
                        file_path=rel_path,
                        line_number=sec.get("line"),
                        title=f"Hardcoded credential detected: {sec.get('description')}",
                        message=f"Suspicious token found on line {sec.get('line')}: {sec.get('matched_sample')}",
                        remediation="Move secret to environment variable (.env) or secret manager.",
                    )
                )

            # 3. Dangerous calls check
            for dc in c_info.get("dangerous_calls", []):
                sev = "error" if self.config.fail_on_dangerous_calls else "warning"
                findings.append(
                    DriftFinding(
                        category="dangerous_call",
                        severity=sev,
                        file_path=rel_path,
                        line_number=dc.get("line"),
                        title=f"Dangerous function execution: {dc.get('call')}()",
                        message=dc.get("description", "Dangerous dynamic call"),
                        remediation=f"Avoid dynamic evaluation with '{dc.get('call')}'; use static dispatch.",
                    )
                )

            # 4. Injected placeholders or empty stubs
            for ph in c_info.get("placeholders", []):
                ph_text = ph.get("text", "")
                if "vibeguard:exempt" in ph_text or "Auto-restored" in ph_text:
                    continue
                kind = ph.get("kind", "")
                if kind == "syntax_error":
                    sev = "error"
                else:
                    sev = "error" if self.config.fail_on_ghost_declarations and kind in ("empty_stub", "duplicate_declaration", "unimplemented_error") else "warning"
                title = f"Duplicate symbol declaration: [{kind}]" if kind == "duplicate_declaration" else f"Unfinished placeholder or stub: [{kind}]"
                remedy = "Consolidate duplicate implementations or rename function to prevent silent overwriting." if kind == "duplicate_declaration" else "Complete implementation before shipping vibe-coded changes."
                findings.append(
                    DriftFinding(
                        category="duplicate_declaration" if kind == "duplicate_declaration" else "stub_injected",
                        severity=sev,
                        file_path=rel_path,
                        line_number=ph.get("line"),
                        title=title,
                        message=ph_text or "Placeholder present",
                        remediation=remedy,
                    )
                )

            # 5. Hallucinated imports
            if self.config.fail_on_hallucinated_imports:
                for imp in c_info.get("imports", []):
                    missing_msg = None
                    if lang == "python" and not self.dep_checker.check_python_import(imp, local_packages):
                        missing_msg = (f"Hallucinated Python package import: '{imp}'", f"Module '{imp}' is not in stdlib, installed in env, nor local project.", f"Add '{imp}' to requirements.txt.")
                    elif lang in ("javascript", "typescript") and not self.dep_checker.check_js_import(imp, local_packages):
                        missing_msg = (f"Hallucinated JS/TS package import: '{imp}'", f"Package '{imp}' is not in package.json nor Node.js built-ins.", f"Run `npm install {imp}`.")
                    elif lang == "go" and not self.dep_checker.check_go_import(imp, local_packages):
                        missing_msg = (f"Hallucinated Go package import: '{imp}'", f"Module '{imp}' is not in Go stdlib, go.mod, nor local packages.", f"Run `go get {imp}`.")
                    elif lang == "rust" and not self.dep_checker.check_rust_import(imp, local_packages):
                        missing_msg = (f"Hallucinated Rust crate import: '{imp}'", f"Crate '{imp}' is not in Rust stdlib, Cargo.toml, nor workspace.", f"Run `cargo add {imp}`.")
                    if missing_msg:
                        findings.append(DriftFinding(
                            category="hallucinated_import",
                            severity="error",
                            file_path=rel_path,
                            line_number=None,
                            title=missing_msg[0],
                            message=missing_msg[1],
                            remediation=missing_msg[2],
                        ))

            # 6. Intent rules & cyclomatic complexity
            self._check_intent_rules(rel_path, c_info, findings)

    def _check_intent_rules(
        self,
        rel_path: str,
        c_info: Dict[str, Any],
        findings: List[DriftFinding],
    ) -> None:
        # Check global complexity threshold
        max_allowed_cc = self.config.max_cyclomatic_complexity
        for sym in c_info.get("symbols", []):
            cc = sym.get("complexity", 1)
            if cc > max_allowed_cc:
                findings.append(
                    DriftFinding(
                        category="rule_violation",
                        severity="warning",
                        file_path=rel_path,
                        line_number=sym.get("line_number"),
                        title=f"High cyclomatic complexity: {sym.get('name')} (complexity {cc} > {max_allowed_cc})",
                        message=f"Function/method '{sym.get('name')}' has cyclomatic complexity of {cc}, exceeding threshold {max_allowed_cc}.",
                        remediation="Refactor branching logic into smaller helper functions to avoid AI hallucination and logic bugs.",
                    )
                )

        # Check explicit user rules
        for rule in self.config.rules:
            if rule.target_patterns:
                matches = any(fnmatch.fnmatch(rel_path, pat) for pat in rule.target_patterns)
                if not matches:
                    continue

            if rule.rule_type == "forbidden_import":
                forbidden = rule.parameters.get("forbidden", [])
                for imp in dict.fromkeys(c_info.get("imports", []) + c_info.get("import_members", [])):
                    if imp in forbidden:
                        findings.append(
                            DriftFinding(
                                category="rule_violation",
                                severity=rule.severity,
                                file_path=rel_path,
                                line_number=None,
                                title=f"Forbidden import violation: {rule.id}",
                                message=f"File imports '{imp}' which is strictly forbidden by policy: {rule.description}",
                                remediation=f"Remove forbidden import '{imp}'.",
                            )
                        )
            elif rule.rule_type == "max_lines":
                limit = rule.parameters.get("max", 200)
                lines = c_info.get("total_lines", 0)
                if lines > limit:
                    findings.append(
                        DriftFinding(
                            category="rule_violation",
                            severity=rule.severity,
                            file_path=rel_path,
                            line_number=lines,
                            title=f"Rule limit exceeded: {rule.id}",
                            message=f"File has {lines} lines, exceeding rule limit of {limit}.",
                            remediation=f"Decompose {rel_path}.",
                        )
                    )
            elif rule.rule_type == "max_complexity":
                limit = rule.parameters.get("max", 15)
                for sym in c_info.get("symbols", []):
                    cc = sym.get("complexity", 1)
                    if cc > limit:
                        findings.append(
                            DriftFinding(
                                category="rule_violation",
                                severity=rule.severity,
                                file_path=rel_path,
                                line_number=sym.get("line_number"),
                                title=f"Complexity rule limit exceeded: {rule.id}",
                                message=f"Symbol '{sym.get('name')}' complexity {cc} exceeds rule limit of {limit}.",
                                remediation="Decompose function branching.",
                            )
                        )
