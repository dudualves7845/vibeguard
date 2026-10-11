"""Remediation guidance generator for AI coding drift.

Generates copy-pasteable instructions and annotation patches. Missing
implementations must be restored or rewritten and audited separately.
"""

import json
from typing import List
from .drift import DriftFinding, DriftReport


class RemediationGenerator:
    """Produces remediation prompts and action items from drift reports."""

    @classmethod
    def generate_ai_prompt(cls, report: DriftReport) -> str:
        """Creates a high-precision prompt to feed back to the AI model."""
        if not report.findings:
            return "✅ No findings were reported in the selected audit scope. This does not prove overall correctness."

        lines = [
            "### 🚨 VIBEGUARD ARCHITECTURAL DRIFT DETECTED 🚨",
            "",
            "The recent AI edits introduced regressions, broken contracts, or unverified changes.",
            "Please apply the following specific corrections to restore codebase health:",
            "",
        ]

        # Group by file
        files_map = {}
        for f in report.findings:
            files_map.setdefault(f.file_path, []).append(f)

        for file_path, findings in files_map.items():
            lines.append(f"#### File: `{file_path}`")
            for item in findings:
                loc = f"Line {item.line_number}: " if item.line_number else ""
                lines.append(f"- **[{item.severity.upper()}] {item.category}**: {loc}{item.title}")
                lines.append(f"  - Issue: {item.message}")
                lines.append(f"  - Fix: {item.remediation}")
            lines.append("")

        lines.extend([
            "### Strict Guidance for Next Iteration:",
            "1. Do NOT delete existing exported public functions/classes.",
            "2. Do NOT import non-existent or uninstalled third-party libraries.",
            "3. Do NOT leave dummy `pass`, `...`, or placeholder comments in place of actual code.",
            "4. NEVER commit or hardcode credentials or secrets.",
        ])

        return "\n".join(lines)

    @classmethod
    def generate_summary(cls, report: DriftReport) -> str:
        """Returns concise human readable summary."""
        return (
            f"Drift Summary: {report.total_findings} finding(s) | "
            f"Critical: {report.critical_count} | Errors: {report.error_count} | Warnings: {report.warning_count}"
        )

    @classmethod
    def export_sarif(cls, report: DriftReport, tool_version: str = "1.0.0") -> dict:
        """Exports drift report as standardized SARIF (Static Analysis Results Interchange Format) v2.1.0."""
        rules_map = {}
        results = []

        sev_to_sarif = {
            "critical": "error",
            "error": "error",
            "warning": "warning",
            "info": "note",
        }

        for idx, finding in enumerate(report.findings):
            rule_id = f"VG-{finding.category.upper().replace('_', '-')}"
            if rule_id not in rules_map:
                rules_map[rule_id] = {
                    "id": rule_id,
                    "name": finding.category,
                    "shortDescription": {"text": finding.title},
                    "fullDescription": {"text": f"{finding.title}. Remediation: {finding.remediation}"},
                    "defaultConfiguration": {
                        "level": sev_to_sarif.get(finding.severity, "warning")
                    },
                }

            result_entry = {
                "ruleId": rule_id,
                "level": sev_to_sarif.get(finding.severity, "warning"),
                "message": {
                    "text": f"{finding.title} — {finding.message}. Fix: {finding.remediation}"
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": finding.file_path,
                                "uriBaseId": "%SRCROOT%",
                            },
                            "region": {
                                "startLine": finding.line_number or 1,
                                "startColumn": 1,
                            },
                        }
                    }
                ],
            }
            results.append(result_entry)

        sarif_doc = {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "VibeGuard",
                            "semanticVersion": tool_version,
                            "informationUri": "https://github.com/antigravity/vibeguard",
                            "rules": list(rules_map.values()),
                        }
                    },
                    "results": results,
                }
            ],
        }
        return sarif_doc

    @classmethod
    def export_github_annotations(cls, report: DriftReport) -> str:
        """Formats findings into GitHub Actions Workflow command annotations.
        
        Outputs ::error file={file},line={line}::{message} or ::warning...
        """
        lines = []
        for finding in report.findings:
            cmd = "error" if finding.severity in ("critical", "error") else "warning"
            file_arg = f"file={cls._escape_workflow_command(finding.file_path, property_value=True)}"
            line_arg = f",line={finding.line_number}" if finding.line_number else ""
            msg = f"{finding.title}: {finding.message} (Remedy: {finding.remediation})"
            lines.append(f"::{cmd} {file_arg}{line_arg}::{cls._escape_workflow_command(msg)}")
        return "\n".join(lines)

    @staticmethod
    def _escape_workflow_command(value: str, property_value: bool = False) -> str:
        escaped = str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        if property_value:
            escaped = escaped.replace(":", "%3A").replace(",", "%2C")
        return escaped

    @classmethod
    def generate_git_patch(
        cls,
        report: DriftReport,
        project_root: str,
        baseline_snapshot=None,
    ) -> str:
        """Generates an applicable guidance patch for missing public contracts.

        Baselines contain metadata, not implementation bodies. The patch adds
        comments describing what to restore; it never invents executable code
        or claims that applying guidance repaired the missing implementation.
        """
        import difflib
        from pathlib import Path

        root = Path(project_root).resolve()
        patch_chunks = []

        if not baseline_snapshot or not baseline_snapshot.files:
            return ""

        # Check for files where public symbols or whole files disappeared
        for rel_path, b_info in baseline_snapshot.files.items():
            file_path = root / rel_path
            try:
                file_path.resolve().relative_to(root)
            except (ValueError, OSError):
                continue

            # Check if file was deleted
            if not file_path.exists():
                # We can't trivially recreate content unless stored, but if baseline stored symbols or raw hash:
                continue

            try:
                with file_path.open(encoding="utf-8", newline="") as source:
                    current_content = source.read()
            except (UnicodeDecodeError, OSError):
                # The patch output is UTF-8. Re-encoding an unknown source
                # encoding would make its context inapplicable or corrupt it.
                # The report's remediation prompt remains available instead.
                continue
            b_symbols = {s["name"]: s for s in b_info.get("symbols", []) if s.get("is_exported", True)}

            # Look for missing symbols in findings
            missing_syms = [
                f for f in report.findings
                if f.file_path == rel_path and f.category == "api_breakage" and "vanished" in f.title
            ]

            if not missing_syms:
                continue

            # Keep symbols missing until the developer restores their real bodies.
            comment = "#" if b_info.get("language") == "python" else "//"
            if b_info.get("language") not in ("python", "javascript", "typescript", "go", "rust", "java"):
                continue
            guidance = []
            for ms in missing_syms:
                sym_name = ms.title.split(": ")[-1].strip()
                # Find symbol info from baseline
                b_sym = b_symbols.get(sym_name, {})
                heading = (f"{comment} VibeGuard remediation guidance: restore "
                           f"{json.dumps(b_sym.get('kind', 'symbol'))} {json.dumps(sym_name)}")
                if heading in current_content:
                    continue
                guidance.extend([
                    heading,
                    f"{comment} Baseline parameters: {json.dumps(b_sym.get('parameters', []))}",
                    f"{comment} TODO: restore the original implementation and scope from version control.",
                ])

            if guidance:
                restored_content = current_content
                if restored_content and not restored_content.endswith("\n"):
                    restored_content += "\n"
                restored_content += "\n" + "\n".join(guidance) + "\n"
                diff = list(
                    difflib.unified_diff(
                        current_content.splitlines(keepends=True),
                        restored_content.splitlines(keepends=True),
                        fromfile=f"a/{rel_path}",
                        tofile=f"b/{rel_path}",
                    )
                )
                if diff:
                    patch_chunks.append("".join(
                        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                        for line in diff
                    ))

        return "\n".join(patch_chunks)
