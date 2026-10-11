"""AST and pattern analyzer for multiple languages (Python, JS/TS, Go, Rust, Java)."""

import ast
import io
import os
from pathlib import Path
import re
import tokenize
from typing import Any, Dict, List, Optional, Set

from .models import FileAnalysis, SymbolInfo
from .analyzer_utils import calculate_cyclomatic_complexity, calculate_shannon_entropy
from .parser_go import GoParser
from .parser_java import JavaParser
from .parser_js import JavaScriptTypeScriptParser
from .parser_rust import RustParser
from .parser_utils import noncode_spans


# High entropy or known secret patterns (e.g. AWS, Stripe, Generic API keys)
SECRET_PATTERNS = [
    (re.compile(r"""(?i)(?:api_key|apikey|secret_key|secret|password|access_token|private_key)["']?\s*[:=]\s*["']([A-Za-z0-9_\-]{16,})["']"""), "High-entropy API key or secret assignment"),
    (re.compile(r"""(?i)AKIA[0-9A-Z]{16}"""), "AWS Access Key ID"),
    (re.compile(r"""(?i)sk_live_[0-9a-zA-Z]{24,}"""), "Stripe Live Secret Key"),
    (re.compile(r"""(?i)ghp_[0-9a-zA-Z]{36}"""), "GitHub Personal Access Token"),
]

# Exclude lines defining regex patterns themselves
_PH_TOKENS = ["TO" + "DO", "FIX" + "ME", "X" + "XX", "HA" + "CK", "NO" + "OP"]
PLACEHOLDER_REGEX = re.compile(r"""(?i)\b(""" + "|".join(_PH_TOKENS) + r""")\b""")


def calculate_shannon_entropy(data: str) -> float:
    """Compatibility wrapper for the public metric helper."""
    from .analyzer_utils import calculate_shannon_entropy as calculate
    return calculate(data)


def calculate_cyclomatic_complexity(node: ast.AST) -> int:
    """Compatibility wrapper for the public AST metric helper."""
    from .analyzer_utils import calculate_cyclomatic_complexity as calculate
    return calculate(node)


class CodeAnalyzer:
    """Multi-language static analyzer for Python, JS/TS, Go, Rust, and Java."""

    @staticmethod
    def detect_language(filepath: str) -> str:
        ext = Path(filepath).suffix.lower()
        if ext in (".py", ".pyw"):
            return "python"
        elif ext in (".js", ".jsx", ".mjs", ".cjs"):
            return "javascript"
        elif ext in (".ts", ".tsx", ".mts", ".cts"):
            return "typescript"
        elif ext in (".go",):
            return "go"
        elif ext in (".rs",):
            return "rust"
        elif ext in (".java",):
            return "java"
        return "unknown"

    @classmethod
    def analyze_file(cls, filepath: str, content: str) -> FileAnalysis:
        lang = cls.detect_language(filepath)
        lines = content.splitlines()
        total_lines = len(lines)

        # Calculate raw hash
        import hashlib
        raw_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        analysis = FileAnalysis(
            path=filepath,
            language=lang,
            total_lines=total_lines,
            raw_hash=raw_hash,
        )

        # 1. Check for hardcoded secrets
        cls._scan_secrets(content, lines, analysis)

        # 2. Check for placeholders
        cls._scan_placeholders(lines, analysis)

        # 3. Parse AST / Symbols / Imports based on language
        if lang == "python":
            cls._analyze_python(content, lines, analysis)
        elif lang in ("javascript", "typescript"):
            cls._analyze_javascript_typescript(content, lines, analysis)
        elif lang == "go":
            cls._analyze_go(content, lines, analysis)
        elif lang == "rust":
            cls._analyze_rust(content, lines, analysis)
        elif lang == "java":
            cls._analyze_java(content, lines, analysis)
        else:
            cls._analyze_generic(lines, analysis)

        return analysis

    @classmethod
    def _scan_secrets(cls, content: str, lines: List[str], analysis: FileAnalysis) -> None:
        for idx, line in enumerate(lines, start=1):
            for pattern, desc in SECRET_PATTERNS:
                matches = pattern.finditer(line)
                for m in matches:
                    matched_val = m.group(1) if m.groups() else m.group(0)
                    if any(t in matched_val.lower() for t in ["test", "dummy", "example", "placeholder", "fake", "xxxx"]):
                        continue
                    if calculate_shannon_entropy(matched_val) < 3.0:
                        continue
                    analysis.secret_findings.append({
                        "line": idx,
                        "description": desc,
                        "matched_sample": matched_val[:4] + "..." + matched_val[-4:] if len(matched_val) > 8 else "***",
                    })

    @classmethod
    def _scan_placeholders(cls, lines: List[str], analysis: FileAnalysis) -> None:
        content = "\n".join(lines)
        comments = []
        if analysis.language == "python":
            try:
                for token in tokenize.generate_tokens(io.StringIO(content).readline):
                    if token.type == tokenize.COMMENT:
                        comments.append((token.start[0], token.string))
            except (tokenize.TokenError, IndentationError, SyntaxError):
                # Syntax errors are reported separately by the AST parser.
                pass
        else:
            for start, end, kind in noncode_spans(content, analysis.language):
                if kind == "comment":
                    comments.extend((content[:start].count("\n") + 1 + offset, line)
                                    for offset, line in enumerate(content[start:end].splitlines()))
        for idx, line in comments:
            stripped = line.strip()
            if PLACEHOLDER_REGEX.search(line):
                if "_PH_TOKENS" in line or "PLACEHOLDER_REGEX" in line or "Auto-restored" in line:
                    continue
                if "stub macros:" in line:
                    continue
                analysis.placeholders.append({
                    "line": idx,
                    "kind": "comment_marker",
                    "text": stripped[:100],
                })

    @classmethod
    def _analyze_python(cls, content: str, lines: List[str], analysis: FileAnalysis) -> None:
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            analysis.placeholders.append({
                "line": e.lineno or 1,
                "kind": "syntax_error",
                "text": f"SyntaxError: {str(e)}",
            })
            return

        class PythonVisitor(ast.NodeVisitor):
            def __init__(self):
                self.scope: List[str] = []
                self.fn_depth = 0
                self.declared_symbols: Dict[str, int] = {}
                self.overload_names = {"overload", "typing.overload", "typing_extensions.overload"}
                self.abstract_names = {"abstractmethod", "abc.abstractmethod"}

            def visit_Call(self, node: ast.Call) -> None:
                # Detect dangerous builtins: eval, exec, __import__, compile
                if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec", "__import__", "compile"):
                    analysis.dangerous_calls.append({
                        "line": node.lineno,
                        "call": node.func.id,
                        "description": f"Dangerous dynamic code execution call: '{node.func.id}()'",
                    })
                self.generic_visit(node)

            def visit_Import(self, node: ast.Import) -> None:
                for alias in node.names:
                    analysis.imports.append(alias.name)
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                if node.level and node.level > 0:
                    self.generic_visit(node)
                    return
                mod = node.module or ""
                if mod:
                    analysis.imports.append(mod)
                    for alias in node.names:
                        if alias.name != "*":
                            analysis.import_members.append(mod + "." + alias.name)
                        if mod in ("typing", "typing_extensions") and alias.name == "overload":
                            self.overload_names.add(alias.asname or alias.name)
                        if mod == "abc" and alias.name == "abstractmethod":
                            self.abstract_names.add(alias.asname or alias.name)
                self.generic_visit(node)

            @staticmethod
            def _effective_body(node: Any) -> List[ast.AST]:
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    value = body[0].value.value
                    if isinstance(value, str):
                        return body[1:]
                return body

            @staticmethod
            def _decorator_name(node: ast.AST) -> str:
                if isinstance(node, ast.Name):
                    return node.id
                if isinstance(node, ast.Attribute):
                    return PythonVisitor._decorator_name(node.value) + "." + node.attr
                return ""

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                full_name = ".".join(self.scope + [node.name])
                doc = ast.get_docstring(node)

                body = self._effective_body(node)
                if len(body) == 1:
                    stmt = body[0]
                    if isinstance(stmt, ast.Pass) or (
                        isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Constant)
                        and stmt.value.value is Ellipsis
                    ):
                        analysis.placeholders.append({
                            "line": node.lineno,
                            "kind": "empty_stub",
                            "text": f"Stub class '{full_name}' has no real implementation (only pass/...)",
                        })

                is_local = self.fn_depth > 0
                is_exported = (not is_local) and (not node.name.startswith("_")) and (not any(part.startswith("_") for part in self.scope))

                analysis.symbols.append(
                    SymbolInfo(
                        name=full_name,
                        kind="class",
                        line_number=node.lineno,
                        docstring=doc,
                        is_exported=is_exported,
                    )
                )

                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def _handle_func(self, node: Any, is_async: bool) -> None:
                kind = "method" if (self.scope and self.fn_depth == 0) else "function"
                full_name = ".".join(self.scope + [node.name])

                decorators = {self._decorator_name(d) for d in node.decorator_list}
                if decorators.intersection(self.overload_names):
                    # Overload signatures are intentional declarations; the runtime
                    # implementation below them is the actual protected function.
                    # Defaults/decorators can still execute when it is declared.
                    self.visit(node.args)
                    for decorator in node.decorator_list:
                        self.visit(decorator)
                    if node.returns is not None:
                        self.visit(node.returns)
                    return
                is_abstract = bool(decorators.intersection(self.abstract_names))
                is_property_part = any(d in (node.name + ".setter", node.name + ".getter", node.name + ".deleter") for d in decorators)

                if full_name in self.declared_symbols and not is_property_part:
                    analysis.placeholders.append({
                        "line": node.lineno,
                        "kind": "duplicate_declaration",
                        "text": f"Duplicate definition of '{full_name}' overwrites earlier declaration at line {self.declared_symbols[full_name]}",
                    })
                else:
                    self.declared_symbols[full_name] = node.lineno

                params = []
                parameter_contract = []
                positional = list(getattr(node.args, "posonlyargs", [])) + list(node.args.args)
                defaults_start = len(positional) - len(node.args.defaults)
                def add_contract(arg, category, default=None):
                    parameter_contract.append({
                        "name": arg.arg,
                        "kind": category,
                        "has_default": default is not None,
                        "default": ast.dump(default) if default is not None else None,
                        "annotation": ast.dump(arg.annotation) if arg.annotation is not None else None,
                    })
                for i, arg in enumerate(positional):
                    default = node.args.defaults[i - defaults_start] if i >= defaults_start else None
                    category = "positional_only" if i < len(getattr(node.args, "posonlyargs", [])) else "positional_or_keyword"
                    add_contract(arg, category, default)

                for arg in getattr(node.args, "posonlyargs", []):
                    params.append(arg.arg)
                for arg in node.args.args:
                    params.append(arg.arg)
                if node.args.vararg:
                    params.append("*" + node.args.vararg.arg)
                    add_contract(node.args.vararg, "var_positional")
                for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                    params.append(arg.arg)
                    add_contract(arg, "keyword_only", default)
                if node.args.kwarg:
                    params.append("**" + node.args.kwarg.arg)
                    add_contract(node.args.kwarg, "var_keyword")

                doc = ast.get_docstring(node)

                body = self._effective_body(node)
                if not is_abstract and not body:
                    analysis.placeholders.append({
                        "line": node.lineno, "kind": "empty_stub",
                        "text": f"Stub {kind} '{full_name}' has only a docstring",
                    })
                elif not is_abstract and len(body) == 1:
                    stmt = body[0]
                    if isinstance(stmt, ast.Pass) or (
                        isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Constant)
                        and stmt.value.value is Ellipsis
                    ):
                        analysis.placeholders.append({
                            "line": node.lineno,
                            "kind": "empty_stub",
                            "text": f"Stub {kind} '{full_name}' has no real implementation (only pass/...)",
                        })
                    elif isinstance(stmt, ast.Raise):
                        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
                        if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                            analysis.placeholders.append({
                                "line": node.lineno,
                                "kind": "unimplemented_error",
                                "text": f"Stub {kind} '{full_name}' raises NotImplementedError",
                            })

                is_local = self.fn_depth > 0
                is_exported = (not is_local) and (not any(part.startswith("_") for part in (self.scope + [node.name])))
                complexity = calculate_cyclomatic_complexity(node)

                analysis.symbols.append(
                    SymbolInfo(
                        name=full_name,
                        kind=kind,
                        line_number=node.lineno,
                        parameters=params,
                        docstring=doc,
                        is_async=is_async,
                        is_exported=is_exported,
                        complexity=complexity,
                        parameter_contract=parameter_contract,
                        return_annotation=ast.dump(node.returns) if node.returns is not None else None,
                    )
                )

                self.scope.append(node.name)
                self.fn_depth += 1
                self.generic_visit(node)
                self.fn_depth -= 1
                self.scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._handle_func(node, is_async=False)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._handle_func(node, is_async=True)

        visitor = PythonVisitor()
        visitor.visit(tree)

    @classmethod
    def _analyze_javascript_typescript(cls, content: str, lines: List[str], analysis: FileAnalysis) -> None:
        JavaScriptTypeScriptParser.parse(content, lines, analysis)

    @classmethod
    def _analyze_go(cls, content: str, lines: List[str], analysis: FileAnalysis) -> None:
        GoParser.parse(lines, analysis)

    @classmethod
    def _analyze_rust(cls, content: str, lines: List[str], analysis: FileAnalysis) -> None:
        RustParser.parse(lines, analysis)

    @classmethod
    def _analyze_java(cls, content: str, lines: List[str], analysis: FileAnalysis) -> None:
        JavaParser.parse(content, lines, analysis)

    @classmethod
    def _analyze_generic(cls, lines: List[str], analysis: FileAnalysis) -> None:
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "/*")):
                continue
            if len(stripped) > 0 and analysis.total_lines == 0:
                analysis.total_lines = len(lines)
