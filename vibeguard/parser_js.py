"""Static parser and symbol extractor for JavaScript and TypeScript source code."""

from pathlib import Path
import re
from typing import List, Optional, Tuple

from .models import FileAnalysis, SymbolInfo
from .parser_utils import mask_noncode


class JavaScriptTypeScriptParser:
    """Parses JavaScript and TypeScript source files: imports, functions, classes, interfaces, types, enums, methods, and stubs."""

    FUNC_REGEX = re.compile(
        r"""(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)(?:<[^>]*>)?\s*\(([\s\S]*?)\)"""
    )
    ARROW_FUNC_REGEX = re.compile(
        r"""(?:export\s+(?:default\s+)?)?(?:const|let|var)\s+([A-Za-z0-9_$]+)(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?(?:\(([\s\S]*?)\)|([A-Za-z0-9_$]+))\s*=>"""
    )
    CLASS_REGEX = re.compile(
        r"""(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z0-9_$]+)"""
    )
    INTERFACE_REGEX = re.compile(
        r"""(?:export\s+)?interface\s+([A-Za-z0-9_$]+)"""
    )
    TYPE_REGEX = re.compile(
        r"""(?:export\s+)?type\s+([A-Za-z0-9_$]+)\s*="""
    )
    ENUM_REGEX = re.compile(
        r"""(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z0-9_$]+)"""
    )
    METHOD_REGEX = re.compile(
        r"""^[ \t]*(?:(?:public|private|protected|static|async|readonly|override)\s+)*([A-Za-z0-9_$]+)\s*\(([\s\S]*?)\)\s*(?::\s*[^\{;]+)?\s*[\{;]"""
    )

    MULTILINE_IMPORT_REGEX = re.compile(
        r"""(?:import\s+(?:[\w*\s{},]*)\s+from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))""",
        re.MULTILINE
    )

    CLEAN_BRACE_PAT = re.compile(
        r"""/\*[\s\S]*?\*/|//[^\n]*|\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'|`([^`\\\\]|\\\\.)*`"""
    )
    DANGEROUS_CALL_PAT = re.compile(
        r"""(?<![\.\$_\w])(?:eval|execScript)\s*\(|(?<![\.\$_\w])new\s+Function\s*\("""
    )

    @classmethod
    def _strip_js_comments_and_strings_for_imports(cls, content: str) -> str:
        return mask_noncode(content, "javascript", mask_strings=False)

    @classmethod
    def _extract_interfaces_types_enums(cls, line: str, idx: int, analysis: FileAnalysis) -> None:
        m_iface = cls.INTERFACE_REGEX.search(line)
        if m_iface:
            analysis.symbols.append(
                SymbolInfo(
                    name=m_iface.group(1),
                    kind="interface",
                    line_number=idx,
                    is_exported="export " in line,
                )
            )
        m_type = cls.TYPE_REGEX.search(line)
        if m_type:
            analysis.symbols.append(
                SymbolInfo(
                    name=m_type.group(1),
                    kind="type",
                    line_number=idx,
                    is_exported="export " in line,
                )
            )
        m_enum = cls.ENUM_REGEX.search(line)
        if m_enum:
            analysis.symbols.append(
                SymbolInfo(
                    name=m_enum.group(1),
                    kind="enum",
                    line_number=idx,
                    is_exported="export " in line,
                )
            )

    @classmethod
    def _merge_signature_line(cls, lines: List[str], line: str, idx: int, current_class: Optional[str]) -> str:
        if "(" not in line:
            return line
        curr = idx - 1
        accum = [line]
        while curr + 1 < len(lines) and " ".join(accum).count("(") > " ".join(accum).count(")"):
            curr += 1
            accum.append(lines[curr])
        if current_class and not any(c in " ".join(accum) for c in ("{", ";")):
            look_curr = curr
            look_accum = list(accum)
            while look_curr + 1 < len(lines) and not any(c in " ".join(look_accum) for c in ("{", ";")):
                look_curr += 1
                next_line = lines[look_curr].strip()
                if not next_line:
                    continue
                look_accum.append(lines[look_curr])
                if "{" in next_line or ";" in next_line:
                    break
            return " ".join(look_accum)
        return " ".join(accum)

    @classmethod
    def _extract_functions_and_methods(
        cls, line: str, merged_sig: str, idx: int, current_class: Optional[str], analysis: FileAnalysis
    ) -> None:
        if current_class:
            m_meth = cls.METHOD_REGEX.match(merged_sig)
            if m_meth:
                meth_name = m_meth.group(1)
                if meth_name not in ("if", "for", "while", "switch", "catch", "return"):
                    raw_params = m_meth.group(2)
                    params = [p.split(":")[0].strip() for p in raw_params.split(",") if p.strip()]
                    is_private = "private " in line or meth_name.startswith("#")
                    analysis.symbols.append(
                        SymbolInfo(
                            name=f"{current_class}.{meth_name}",
                            kind="method",
                            line_number=idx,
                            parameters=params,
                            is_exported=not is_private,
                            is_async="async " in line,
                        )
                    )

        m_func = cls.FUNC_REGEX.search(merged_sig)
        if m_func:
            name = m_func.group(1)
            raw_params = m_func.group(2)
            params = [p.split(":")[0].strip() for p in raw_params.split(",") if p.strip()]
            analysis.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="function",
                    line_number=idx,
                    parameters=params,
                    is_exported="export " in line,
                    is_async="async " in line,
                )
            )

        m_arrow = cls.ARROW_FUNC_REGEX.search(merged_sig)
        if m_arrow:
            name = m_arrow.group(1)
            raw_params = m_arrow.group(2) or m_arrow.group(3) or ""
            params = [p.split(":")[0].strip() for p in raw_params.split(",") if p.strip()]
            analysis.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="function",
                    line_number=idx,
                    parameters=params,
                    is_exported="export " in line,
                    is_async="async " in line,
                )
            )

    EMPTY_FUNC_PATTERNS = [
        ("function", re.compile(
            r"""(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)(?:<[^>]*>)?\s*\(([\s\S]*?)\)(?:\s*:\s*[^\{]+)?\s*\{"""
        )),
        ("arrow function", re.compile(
            r"""(?:export\s+(?:default\s+)?)?(?:const|let|var)\s+([A-Za-z0-9_$]+)(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?(?:\(([\s\S]*?)\)|([A-Za-z0-9_$]+))(?:\s*:\s*[^=]+)?\s*=>\s*\{"""
        )),
        ("method", re.compile(
            r"""^[ \t]*(?:(?:public|private|protected|static|async|readonly|override)\s+)*([A-Za-z0-9_$]+)\s*\(([\s\S]*?)\)\s*(?::\s*[^\{;]+)?\s*\{""",
            re.MULTILINE
        )),
    ]

    MASK_PAT = re.compile(
        r"""/\*[\s\S]*?\*/|//[^\n]*|\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'|`([^`\\\\]|\\\\.)*`"""
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
    def _scan_empty_stubs(cls, content: str, analysis: FileAnalysis) -> None:
        masked = mask_noncode(content, "javascript")
        without_comments = mask_noncode(content, "javascript", mask_strings=False)
        seen: set = set()
        for kind, pat in cls.EMPTY_FUNC_PATTERNS:
            for m in pat.finditer(masked):
                name = m.group(1)
                if name in ("if", "for", "while", "switch", "catch", "return"):
                    continue
                brace_start = m.end() - 1
                brace_end = cls._find_matching_brace(masked, brace_start)
                if brace_end != -1:
                    clean_body = without_comments[brace_start + 1:brace_end].strip()
                    if clean_body == "":
                        line_no = content[:m.start()].count("\n") + 1
                        key = (line_no, name)
                        if key not in seen:
                            seen.add(key)
                            analysis.placeholders.append({
                                "line": line_no,
                                "kind": "empty_stub",
                                "text": f"Empty stub {kind} '{name}' has no implementation",
                            })

    @classmethod
    def parse(cls, content: str, lines: List[str], analysis: FileAnalysis) -> None:
        code_content = mask_noncode(content, "javascript")
        code_lines = code_content.splitlines()
        cleaned_for_imports = cls._strip_js_comments_and_strings_for_imports(content)
        for m in cls.MULTILINE_IMPORT_REGEX.finditer(cleaned_for_imports):
            if not code_content[m.start():m.start() + 1].strip():
                continue
            imp = m.group(1) or m.group(2) or m.group(3)
            if imp and imp not in analysis.imports:
                analysis.imports.append(imp)

        cls._scan_empty_stubs(content, analysis)

        current_class: Optional[str] = None
        brace_depth = 0

        for idx, line in enumerate(code_lines, start=1):
            cls._extract_interfaces_types_enums(line, idx, analysis)
            merged_sig = cls._merge_signature_line(code_lines, line, idx, current_class)

            m_class = cls.CLASS_REGEX.search(line)
            if m_class:
                current_class = m_class.group(1)
                analysis.symbols.append(
                    SymbolInfo(
                        name=current_class,
                        kind="class",
                        line_number=idx,
                        is_exported="export " in line,
                    )
                )
            else:
                cls._extract_functions_and_methods(line, merged_sig, idx, current_class, analysis)

            clean_for_braces = line
            brace_depth += clean_for_braces.count("{") - clean_for_braces.count("}")
            if brace_depth <= 0:
                current_class = None
                brace_depth = 0

            if "throw new Error" in line and re.search(r"""throw new Error\(['"]not implemented['"]\)""", lines[idx - 1], re.IGNORECASE):
                analysis.placeholders.append({
                    "line": idx,
                    "kind": "unimplemented_error",
                    "text": lines[idx - 1].strip(),
                })

            # Check for dangerous dynamic execution calls in non-comment/string parts
            m_danger = cls.DANGEROUS_CALL_PAT.search(clean_for_braces)
            if m_danger:
                matched_token = m_danger.group(0).split("(")[0].strip()
                analysis.dangerous_calls.append({
                    "line": idx,
                    "call": matched_token,
                    "description": f"Dangerous dynamic JavaScript execution call: '{matched_token}()'",
                })
