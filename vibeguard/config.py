"""Configuration, Intentions and Rule Contracts for VibeGuard."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _strings(value: Any, name: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")


def _positive_int(value: Any, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class IntentRule:
    """An intention or invariant rule."""
    id: str
    description: str
    rule_type: str  # 'immutability', 'forbidden_import', 'max_lines', 'security', 'pure_function', 'max_complexity'
    target_patterns: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    severity: str = "error"  # 'error', 'warning'

    def __post_init__(self) -> None:
        for name in ("id", "description", "rule_type"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"Rule {name} must be a string")
        _strings(self.target_patterns, "Rule target_patterns")
        if not isinstance(self.parameters, dict):
            raise ValueError("Rule parameters must be an object")
        if self.severity not in ("critical", "error", "warning"):
            raise ValueError("Rule severity must be critical, error or warning")
        if self.rule_type in ("max_lines", "max_complexity") and "max" in self.parameters:
            _positive_int(self.parameters["max"], "Rule max")
        if self.rule_type == "forbidden_import" and "forbidden" in self.parameters:
            _strings(self.parameters["forbidden"], "Rule forbidden")


@dataclass(frozen=True)
class GuardConfig:
    """Project-level VibeGuard configuration."""
    project_name: str
    protected_paths: List[str] = field(
        default_factory=lambda: ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.go", "*.rs", "*.java"]
    )
    ignored_paths: List[str] = field(
        default_factory=lambda: [
            ".git/*",
            "node_modules/*",
            "__pycache__/*",
            ".venv/*",
            "venv/*",
            "*.pyc",
            ".vibeguard/*",
            "tests/*",
            "target/*",
            "build/*",
        ]
    )
    rules: List[IntentRule] = field(default_factory=list)
    max_file_lines: int = 400
    fail_on_hallucinated_imports: bool = True
    fail_on_ghost_declarations: bool = True
    fail_on_secret_leak: bool = True
    fail_on_dangerous_calls: bool = False
    max_cyclomatic_complexity: int = 25

    def __post_init__(self) -> None:
        if not isinstance(self.project_name, str):
            raise ValueError("project_name must be a string")
        _strings(self.protected_paths, "protected_paths")
        _strings(self.ignored_paths, "ignored_paths")
        _positive_int(self.max_file_lines, "max_file_lines")
        _positive_int(self.max_cyclomatic_complexity, "max_cyclomatic_complexity")
        for name in ("fail_on_hallucinated_imports", "fail_on_ghost_declarations", "fail_on_secret_leak", "fail_on_dangerous_calls"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if not isinstance(self.rules, list) or not all(isinstance(rule, IntentRule) for rule in self.rules):
            raise ValueError("rules must be a list of rule objects")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "protected_paths": self.protected_paths,
            "ignored_paths": self.ignored_paths,
            "max_file_lines": self.max_file_lines,
            "fail_on_hallucinated_imports": self.fail_on_hallucinated_imports,
            "fail_on_ghost_declarations": self.fail_on_ghost_declarations,
            "fail_on_secret_leak": self.fail_on_secret_leak,
            "fail_on_dangerous_calls": self.fail_on_dangerous_calls,
            "max_cyclomatic_complexity": self.max_cyclomatic_complexity,
            "rules": [
                {
                    "id": r.id,
                    "description": r.description,
                    "rule_type": r.rule_type,
                    "target_patterns": r.target_patterns,
                    "parameters": r.parameters,
                    "severity": r.severity,
                }
                for r in self.rules
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GuardConfig":
        if not isinstance(data, dict):
            raise ValueError("Configuration must be an object")
        raw_rules = data.get("rules", [])
        if not isinstance(raw_rules, list) or not all(isinstance(rule, dict) for rule in raw_rules):
            raise ValueError("rules must be a list of rule objects")
        defaults = cls(project_name="unnamed_project")
        rules = [
            IntentRule(
                id=r.get("id", ""),
                description=r.get("description", ""),
                rule_type=r.get("rule_type", "custom"),
                target_patterns=r.get("target_patterns", []),
                parameters=r.get("parameters", {}),
                severity=r.get("severity", "error"),
            )
            for r in raw_rules
        ]
        return cls(
            project_name=data.get("project_name", "unnamed_project"),
            protected_paths=data.get("protected_paths", defaults.protected_paths),
            ignored_paths=data.get("ignored_paths", defaults.ignored_paths),
            max_file_lines=data.get("max_file_lines", defaults.max_file_lines),
            fail_on_hallucinated_imports=data.get("fail_on_hallucinated_imports", defaults.fail_on_hallucinated_imports),
            fail_on_ghost_declarations=data.get("fail_on_ghost_declarations", defaults.fail_on_ghost_declarations),
            fail_on_secret_leak=data.get("fail_on_secret_leak", defaults.fail_on_secret_leak),
            fail_on_dangerous_calls=data.get("fail_on_dangerous_calls", defaults.fail_on_dangerous_calls),
            max_cyclomatic_complexity=data.get("max_cyclomatic_complexity", defaults.max_cyclomatic_complexity),
            rules=rules,
        )

    @classmethod
    def load_from_file(cls, path: str) -> "GuardConfig":
        p = Path(path)
        if not p.exists():
            return cls(project_name=p.parent.name or "project")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def save_to_file(self, path: str) -> None:
        # Validate again because the lists/dicts inside a frozen config are mutable.
        self.from_dict(self.to_dict())
        serialized = json.dumps(self.to_dict(), indent=2)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(serialized)
