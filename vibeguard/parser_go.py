"""Static parser and symbol extractor for Go source code."""

from pathlib import Path
import re
from typing import List

from .models import FileAnalysis, SymbolInfo
from .parser_utils import mask_noncode


class GoParser:
    """Parses Go source files: imports, structs, interfaces, methods, functions, and panic stubs."""

    IMPORT_BLOCK_REGEX = re.compile(r"""^\s*import\s*\(""")
    SINGLE_IMPORT_REGEX = re.compile(r"""^\s*import\s+(?:[A-Za-z0-9_.]+\s+)?["']([^"']+)["']""")
    PKG_IMPORT_LINE = re.compile(r"""^\s*(?:[A-Za-z0-9_.]+\s+)?["']([^"']+)["']""")
    FUNC_REGEX = re.compile(r"""^func\s+(?:\((?:[A-Za-z0-9_*]+\s+)?([*]?[A-Za-z0-9_]+)(?:\[[^\]]*\])?\)\s+)?([A-Za-z0-9_]+)(?:\[[^\]]*\])?\s*\(([^)]*)\)""")
    TYPE_REGEX = re.compile(r"""^type\s+([A-Za-z0-9_]+)(?:\[[^\]]*\])?\s+(struct|interface)""")

    EMPTY_FUNC_REGEX = re.compile(
        r"""^[ \t]*func\s+(?:\((?:[A-Za-z0-9_*]+\s+)?([*]?[A-Za-z0-9_]+)(?:\[[^\]]*\])?\)\s+)?([A-Za-z0-9_]+)(?:\[[^\]]*\])?\s*\(([\s\S]*?)\)[^\{]*\{""",
        re.MULTILINE
    )

    MASK_PAT = re.compile(
        r"""/\*[\s\S]*?\*/|//[^\n]*|\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'|`([^`]*)`"""
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
        masked = mask_noncode(full_content, "go")
        without_comments = mask_noncode(full_content, "go", mask_strings=False)
        seen = set()
        for m in cls.EMPTY_FUNC_REGEX.finditer(masked):
            receiver = m.group(1)
            fn_name = m.group(2)
            brace_start = m.end() - 1
            brace_end = cls._find_matching_brace(masked, brace_start)
            if brace_end != -1:
                clean = without_comments[brace_start + 1:brace_end].strip()
                if clean == "":
                    full_name = f"{receiver.lstrip('*')}.{fn_name}" if receiver else fn_name
                    kind = "method" if receiver else "function"
                    line_no = full_content[:m.start()].count("\n") + 1
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
        cls._scan_empty_stubs(full_content, analysis)
        code_lines = mask_noncode(full_content, "go").splitlines()
        import_lines = mask_noncode(full_content, "go", mask_strings=False).splitlines()
        in_import_block = False

        for idx, line in enumerate(code_lines, start=1):
            stripped = line.strip()
            import_line = import_lines[idx - 1].strip()

            if cls.IMPORT_BLOCK_REGEX.match(stripped):
                in_import_block = True
                continue
            if in_import_block:
                if stripped == ")":
                    in_import_block = False
                    continue
                m_imp = cls.PKG_IMPORT_LINE.match(import_line)
                if m_imp:
                    imp_val = m_imp.group(1)
                    if imp_val not in analysis.imports:
                        analysis.imports.append(imp_val)
                continue

            m_single = cls.SINGLE_IMPORT_REGEX.match(import_line) if stripped.startswith("import") else None
            if m_single:
                imp_val = m_single.group(1)
                if imp_val not in analysis.imports:
                    analysis.imports.append(imp_val)

            m_type = cls.TYPE_REGEX.match(stripped)
            if m_type:
                type_name = m_type.group(1)
                kind = m_type.group(2)
                is_exported = type_name[0].isupper() if type_name else False
                analysis.symbols.append(
                    SymbolInfo(
                        name=type_name,
                        kind=kind,
                        line_number=idx,
                        is_exported=is_exported,
                    )
                )

            # Support single and multiline function/method signatures
            merged_fn_line = stripped
            if stripped.startswith("func ") and "(" in stripped:
                curr = idx - 1
                accum = [stripped]
                while curr + 1 < len(code_lines) and " ".join(accum).count("(") > " ".join(accum).count(")"):
                    curr += 1
                    accum.append(code_lines[curr].strip())
                merged_fn_line = " ".join(accum)

            m_fn = cls.FUNC_REGEX.match(merged_fn_line)
            if m_fn:
                receiver = m_fn.group(1)
                fn_name = m_fn.group(2)
                raw_params = m_fn.group(3)
                params = [p.strip().split()[0] for p in raw_params.split(",") if p.strip()]

                full_name = f"{receiver.lstrip('*')}.{fn_name}" if receiver else fn_name
                is_exported = fn_name[0].isupper() if fn_name else False
                analysis.symbols.append(
                    SymbolInfo(
                        name=full_name,
                        kind="method" if receiver else "function",
                        line_number=idx,
                        parameters=params,
                        is_exported=is_exported,
                    )
                )

            if "panic(" in stripped and re.search(r"""panic\(['"](?:not implemented|todo|unimplemented)['"]\)""", import_line, re.IGNORECASE):
                analysis.placeholders.append({
                    "line": idx,
                    "kind": "unimplemented_error",
                    "text": import_line,
                })
