"""Static parser and symbol extractor for Java source code."""

from pathlib import Path
import re
from typing import List, Optional

from .models import FileAnalysis, SymbolInfo
from .parser_utils import mask_noncode, split_parameters


class JavaParser:
    """Parses Java source files: package, imports, classes, interfaces, records, enums, methods, and stubs."""

    PACKAGE_REGEX = re.compile(r"""^\s*package\s+([A-Za-z0-9_.]+)\s*;""")
    IMPORT_REGEX = re.compile(r"""^\s*import\s+(?:static\s+)?([A-Za-z0-9_.*]+)\s*;""")
    TYPE_REGEX = re.compile(
        r"""^\s*(?:(?:public|protected|private|abstract|static|final)\s+)*(class|interface|enum|record)\s+([A-Za-z0-9_$]+)"""
    )
    METHOD_REGEX = re.compile(
        r"""^\s*(?:(?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)*(?:<[^>]+>\s+)?([A-Za-z0-9_$.?<>\[\],\s]+?)\s+([A-Za-z0-9_$]+)\s*\(([\s\S]*?)\)\s*(?:throws\s+[A-Za-z0-9_$.,\s]+)?\s*[\{;]"""
    )
    CONSTRUCTOR_REGEX = re.compile(
        r"""^\s*(?:(?:public|protected|private)\s+)*([A-Za-z0-9_$]+)\s*\(([\s\S]*?)\)\s*(?:throws\s+[A-Za-z0-9_$,\s]+)?\s*[\{;]"""
    )

    CLEAN_BRACE_PAT = re.compile(
        r"""/\*[\s\S]*?\*/|//[^\n]*|\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'"""
    )

    @classmethod
    def _parse_method_signature(
        cls, stripped: str, idx: int, code_content: str, code_lines: List[str],
        line_offsets: List[int], without_comments: str, current_type: Optional[str],
        current_kind: Optional[str], analysis: FileAnalysis,
    ) -> None:
        # Method / Constructor
        if "(" in stripped and not stripped.startswith(("throw ", "return ")):
            curr = idx - 1
            accum = [stripped]
            while curr + 1 < len(code_lines) and " ".join(accum).count("(") > " ".join(accum).count(")"):
                curr += 1
                accum.append(code_lines[curr].strip())
            merged_sig = " ".join(accum)

            m_meth = cls.METHOD_REGEX.match(merged_sig)
            m_ctor = cls.CONSTRUCTOR_REGEX.match(merged_sig) if not m_meth else None

            matched_name = None
            raw_params = ""
            if m_meth:
                matched_name = m_meth.group(2).strip()
                raw_params = m_meth.group(3).strip()
            elif m_ctor and current_type and m_ctor.group(1).strip() == current_type:
                matched_name = m_ctor.group(1).strip()
                raw_params = m_ctor.group(2).strip()

            if matched_name and matched_name not in ("if", "for", "while", "switch", "catch", "synchronized", "throw"):
                params = []
                parameter_contract = []
                if raw_params:
                    for param in split_parameters(raw_params):
                        param = re.sub(r"@[A-Za-z0-9_$.]+(?:\s*\([^)]*\))?\s*", "", param)
                        parts = param.strip().split()
                        if parts:
                            param_name = parts[-1].strip()
                            params.append(param_name.rstrip("[]"))
                            param_type = " ".join(p for p in parts[:-1] if p != "final")
                            if param_name.endswith("[]"):
                                param_type += "[]"
                            param_type = re.sub(r"\s*([<>,\[\]])\s*", r"\1", param_type)
                            parameter_contract.append({"name": params[-1], "type": param_type})
                full_name = f"{current_type}.{matched_name}" if current_type else matched_name
                is_exported = "public " in merged_sig or "protected " in merged_sig or (current_kind == "interface" and "private " not in merged_sig)
                analysis.symbols.append(
                    SymbolInfo(
                        name=full_name,
                        kind="method" if current_type else "function",
                        line_number=idx,
                        parameters=params,
                        is_exported=is_exported,
                        parameter_contract=parameter_contract,
                        return_annotation=m_meth.group(1).strip() if m_meth else None,
                        signature="(" + ",".join(p["type"] for p in parameter_contract) + ")",
                    )
                )
                if merged_sig.rstrip().endswith("{"):
                    body_start = code_content.find("{", line_offsets[idx - 1])
                else:
                    body_start = code_content.find("{", line_offsets[idx - 1], line_offsets[curr] + len(code_lines[curr])) if "{" in merged_sig else -1
                if body_start >= 0:
                    depth = 1
                    end = body_start + 1
                    while end < len(code_content) and depth:
                        depth += (code_content[end] == "{") - (code_content[end] == "}")
                        end += 1
                    if depth == 0 and not without_comments[body_start + 1:end - 1].strip():
                        analysis.placeholders.append({
                            "line": idx, "kind": "empty_stub",
                            "text": f"Empty stub method '{full_name}' has no implementation",
                        })

    @classmethod
    def parse(cls, content: str, lines: List[str], analysis: FileAnalysis) -> None:
        current_type: Optional[str] = None
        current_kind: Optional[str] = None
        brace_depth = 0
        code_content = mask_noncode(content, "java")
        code_lines = code_content.splitlines()
        without_comments = mask_noncode(content, "java", mask_strings=False)
        line_offsets = []
        offset = 0
        for line in content.splitlines(keepends=True):
            line_offsets.append(offset)
            offset += len(line)

        for idx, line in enumerate(code_lines, start=1):
            stripped = line.strip()
            # Inline method/type annotations do not alter their visibility.
            stripped = re.sub(r"@[A-Za-z0-9_$.]+(?:\s*\([^)]*\))?\s*", "", stripped)

            # Imports
            m_imp = cls.IMPORT_REGEX.match(stripped)
            if m_imp:
                imp = m_imp.group(1).rstrip(".*")
                if imp and imp not in analysis.imports:
                    analysis.imports.append(imp)
                continue

            # Class / Interface / Enum / Record
            m_type = cls.TYPE_REGEX.match(stripped)
            if m_type:
                kind = m_type.group(1)
                current_kind = kind
                name = m_type.group(2)
                is_exported = "public " in stripped or "protected " in stripped
                current_type = name
                analysis.symbols.append(
                    SymbolInfo(
                        name=name,
                        kind=kind,
                        line_number=idx,
                        is_exported=is_exported,
                    )
                )

            cls._parse_method_signature(
                stripped, idx, code_content, code_lines, line_offsets,
                without_comments, current_type, current_kind, analysis,
            )

            # Update brace depth
            clean_for_braces = line
            brace_depth += clean_for_braces.count("{") - clean_for_braces.count("}")
            if brace_depth <= 0:
                current_type = None
                current_kind = None
                brace_depth = 0

            # Detect unsupported operation exceptions or stubs
            if re.search(
                r"""throw\s+new\s+(?:UnsupportedOperationException|NotImplementedException)\s*\(""",
                stripped,
                re.IGNORECASE,
            ):
                analysis.placeholders.append({
                    "line": idx,
                    "kind": "unimplemented_error",
                    "text": stripped,
                })
