"""JSON-compatible drift report serialization."""

from typing import Any, Dict


def report_to_dict(report: Any) -> Dict[str, Any]:
    return {
        "total_findings": report.total_findings,
        "critical_count": report.critical_count,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "has_blocking_errors": report.has_blocking_errors,
        "has_baseline": report.has_baseline,
        "baseline_timestamp": report.baseline_timestamp,
        "baseline_files_count": report.baseline_files_count,
        "baseline_symbols_count": report.baseline_symbols_count,
        "findings": [
            {
                "category": finding.category,
                "severity": finding.severity,
                "file_path": finding.file_path,
                "line_number": finding.line_number,
                "title": finding.title,
                "message": finding.message,
                "remediation": finding.remediation,
            }
            for finding in report.findings
        ],
    }
