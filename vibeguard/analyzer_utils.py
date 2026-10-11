"""Reusable metrics for source analysis."""

import ast
import math
from typing import Dict


def calculate_shannon_entropy(data: str) -> float:
    """Calculate Shannon entropy for a candidate secret."""
    if not data:
        return 0.0
    length = len(data)
    counts: Dict[str, int] = {}
    for char in data:
        counts[char] = counts.get(char, 0) + 1
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def calculate_cyclomatic_complexity(node: ast.AST) -> int:
    """Compute cyclomatic complexity scoped to one Python function."""
    complexity = 1
    stack = list(ast.iter_child_nodes(node))
    while stack:
        curr = stack.pop()
        if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(curr, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler, ast.With, ast.AsyncWith)):
            complexity += 1
        elif isinstance(curr, ast.BoolOp):
            complexity += len(curr.values) - 1
        elif hasattr(ast, "Match") and isinstance(curr, ast.Match):
            complexity += len(getattr(curr, "cases", []))
        stack.extend(ast.iter_child_nodes(curr))
    return complexity
