"""Data structures for static analysis results."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SymbolInfo:
    """Represents a declared symbol (function, class, method, interface, struct, etc.)."""
    name: str
    kind: str  # 'function', 'class', 'method', 'interface', 'struct', 'trait', 'type'
    line_number: int
    parameters: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    is_async: bool = False
    is_exported: bool = False
    complexity: int = 1
    # None marks metadata absent in legacy baselines.
    parameter_contract: Optional[List[Dict[str, Any]]] = None
    return_annotation: Optional[str] = None
    signature: Optional[str] = None


@dataclass
class FileAnalysis:
    """Complete structural extraction of a source file."""
    path: str
    language: str
    total_lines: int
    symbols: List[SymbolInfo] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    placeholders: List[Dict[str, Any]] = field(default_factory=list)
    secret_findings: List[Dict[str, Any]] = field(default_factory=list)
    dangerous_calls: List[Dict[str, Any]] = field(default_factory=list)
    raw_hash: str = ""
    import_members: List[str] = field(default_factory=list)
