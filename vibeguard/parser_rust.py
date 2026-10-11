"""Static parser and symbol extractor for Rust source code."""

from pathlib import Path
import re
from typing import List, Optional

from .models import FileAnalysis, SymbolInfo
from .parser_utils import mask_noncode


class RustParser:
    """Parses Rust source files: use crates, structs, enums, traits, functions, methods, and todo!() stubs."""

    USE_MULTILINE_BLOCK_REGEX = re.compile(r"""^\s*(?:pub\s+)?use\s+(?:([A-Za-z0-9_]+)::)?\{\s*$""")
    USE_STMT_REGEX = re.compile(r"""\b(?:pub(?:\s*\([^)]+\))?\s+)?use\s+([^;]+);""")
    FN_REGEX = re.compile(r"""^\s*(pub(?:\s*\([^)]+\))?\s+)?(?:async\s+)?(?:const\s+)?fn\s+([A-Za-z0-9_]+)\s*(?:<[^>]*>)?\s*\(([\s\S]*?)\)""")
    STRUCT_ENUM_TRAIT_REGEX = re.compile(r"""^\s*(pub(?:\s*\([^)]+\))?\s+)?(struct|enum|trait)\s+([A-Za-z0-9_]+)""")
    IMPL_REGEX = re.compile(r"""^\s*impl(?:\s*<[^>]*>)?(?:\s+[A-Za-z0-9_]+(?:<[^>]*>)?\s+for)?\s+([A-Za-z0-9_]+)(?:<[^>]*>)?""")
    TODO_MACRO_REGEX = re.compile(r"""\b(?:todo!|unimplemented!)\s*(?:[({[]|$)""")

    @classmethod
    def _extract_use_crates(cls, full_content: str, analysis: FileAnalysis) -> None:
        clean = mask_noncode(full_content, "rust")
        for m in cls.USE_STMT_REGEX.finditer(clean):
            raw = m.group(1).strip()
            # Case 1: prefix::{ ... } e.g. tokio::{sync::mpsc, net::TcpStream}
            m_prefix = re.match(r"""^([A-Za-z0-9_]+)::\{([\s\S]*)\}$""", raw)
            if m_prefix:
                prefix = m_prefix.group(1)
                if prefix not in ("crate", "super", "self") and prefix not in analysis.imports:
                    analysis.imports.append(prefix)
                continue
            # Case 2: { ... } e.g. { serde::Serialize, tokio::sync::mpsc }
            m_block = re.match(r"""^\{([\s\S]*)\}$""", raw)
            if m_block:
                inner = m_block.group(1)
                for item in inner.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    first = item.split("::")[0].strip()
                    if first and first not in ("crate", "super", "self") and first not in analysis.imports:
                        analysis.imports.append(first)
                continue
            # Case 3: standard use e.g. std::collections::HashMap or serde::Serialize
            first = raw.split("::")[0].strip()
            if first and first not in ("crate", "super", "self") and first not in analysis.imports:
                analysis.imports.append(first)

    EMPTY_FN_REGEX = re.compile(
        r"""^[ \t]*(?:pub(?:\s*\([^)]+\))?\s+)?(?:async\s+)?(?:const\s+)?fn\s+([A-Za-z0-9_]+)(?:<[^>]*>)?\s*\(([\s\S]*?)\)[^\{;]*\{""",
        re.MULTILINE
    )

    MASK_PAT = re.compile(
        r"""/\*[\s\S]*?\*/|//[^\n]*|\"(?:[^\"\\\\]|\\\\.)*\"|r#*\"[\s\S]*?\"#*"""
    )

    @staticmethod
    def _find_matching_brace(masked_text: str, start_pos: int) -> int:
        depth = 0
        for i in range(start_pos, len(masked_text)):
            ch = masked_text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        return -1

    @classmethod
    def _scan_empty_stubs(cls, full_content: str, analysis: FileAnalysis) -> None:
        masked = mask_noncode(full_content, "rust")
        without_comments = mask_noncode(full_content, "rust", mask_strings=False)
        impl_pat = re.compile(
            r"""^[ \t]*impl(?:\s*<[^>]*>)?(?:\s+[A-Za-z0-9_]+(?:<[^>]*>)?\s+for)?\s+([A-Za-z0-9_]+)(?:<[^>]*>)?\s*\{""",
            re.MULTILINE
        )
        impl_spans = []
        for m in impl_pat.finditer(masked):
            b_start = m.end() - 1
            b_end = cls._find_matching_brace(masked, b_start)
            if b_end != -1:
                impl_spans.append((b_start, b_end, m.group(1)))

        seen = set()
        for m in cls.EMPTY_FN_REGEX.finditer(masked):
            fn_name = m.group(1)
            brace_start = m.end() - 1
            brace_end = cls._find_matching_brace(masked, brace_start)
            if brace_end != -1:
                clean = without_comments[brace_start + 1:brace_end].strip()
                if clean == "":
                    line_no = full_content[:m.start()].count("\n") + 1
                    parent_impl = None
                    for s_start, s_end, s_type in impl_spans:
                        if s_start < m.start() < s_end:
                            parent_impl = s_type
                            break
                    full_name = f"{parent_impl}.{fn_name}" if parent_impl else fn_name
                    kind = "method" if parent_impl else "function"
                    key = (line_no, full_name)
                    if key not in seen:
                        seen.add(key)
                        analysis.placeholders.append({
                            "line": line_no,
                            "kind": "empty_stub",
                            "text": f"Empty stub {kind} '{full_name}' has no implementation",
                        })

    @classmethod
    def parse(cls, lines: List[str], analysis: FileAnalysis) -> None:
        full_content = "\n".join(lines)
        cls._extract_use_crates(full_content, analysis)
        cls._scan_empty_stubs(full_content, analysis)
        code_lines = mask_noncode(full_content, "rust").splitlines()

        current_impl: Optional[str] = None
        brace_depth = 0

        for idx, line in enumerate(code_lines, start=1):
            stripped = line.strip()

            m_impl = cls.IMPL_REGEX.match(stripped)
            if m_impl:
                current_impl = m_impl.group(1)

            m_set = cls.STRUCT_ENUM_TRAIT_REGEX.match(stripped)
            if m_set:
                is_pub = bool(m_set.group(1))
                kind = m_set.group(2)
                type_name = m_set.group(3)
                analysis.symbols.append(
                    SymbolInfo(
                        name=type_name,
                        kind=kind,
                        line_number=idx,
                        is_exported=is_pub,
                    )
                )

            # Support single and multiline function/method signatures
            merged_fn_line = stripped
            if re.search(r"""\bfn\s+[A-Za-z0-9_]+""", stripped) and "(" in stripped:
                curr = idx - 1
                accum = [stripped]
                while curr + 1 < len(code_lines) and " ".join(accum).count("(") > " ".join(accum).count(")"):
                    curr += 1
                    accum.append(code_lines[curr].strip())
                merged_fn_line = " ".join(accum)

            m_fn = cls.FN_REGEX.match(merged_fn_line)
            if m_fn:
                is_pub = bool(m_fn.group(1))
                fn_name = m_fn.group(2)
                raw_params = m_fn.group(3)
                params = [p.strip().split(":")[0].strip() for p in raw_params.split(",") if p.strip()]

                full_name = f"{current_impl}.{fn_name}" if current_impl else fn_name
                analysis.symbols.append(
                    SymbolInfo(
                        name=full_name,
                        kind="method" if current_impl else "function",
                        line_number=idx,
                        parameters=params,
                        is_exported=is_pub,
                    )
                )

            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                current_impl = None
                brace_depth = 0

            if cls.TODO_MACRO_REGEX.search(stripped):
                original_line = lines[idx - 1].strip()
                if "vibeguard:exempt" not in original_line and "Auto-restored" not in original_line:
                    analysis.placeholders.append({
                        "line": idx,
                        "kind": "unimplemented_error",
                        "text": original_line,
                    })
