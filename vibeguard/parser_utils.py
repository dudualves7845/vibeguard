"""Position-preserving lexical masks for the lightweight C-family parsers."""

import re
from typing import Any, Dict, Iterator, List, Tuple


def _regex_end(content: str, start: int) -> int:
    """Find a JS regex terminator, respecting escapes and character classes."""
    i = start + 1
    in_class = False
    while i < len(content) and content[i] not in "\r\n":
        char = content[i]
        if char == "\\":
            i += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            i += 1
            while i < len(content) and content[i].isalpha():
                i += 1
            return i
        i += 1
    return -1


def _javascript_identifier(content: str, start: int, state: Dict[str, Any]) -> int:
    end = start + 1
    while end < len(content) and (content[end].isalnum() or content[end] in "_$"):
        end += 1
    word = content[start:end]
    member = state["last_token"] == "."
    if word in ("break", "continue") and not member:
        state["restricted"] = True
    if word == "await" and state["last_token"] == "for":
        return end
    if word == "function" and not member:
        state["pending_function"] = state["last_token"] in state["expression_prefixes"]
    elif word == "class" and not member:
        state["pending_class"] = state["last_token"] in state["expression_prefixes"]
    state["allow_regex"] = not member and word in {
        "return", "throw", "yield", "await", "case", "delete", "void", "typeof",
        "new", "in", "instanceof", "of", "else", "do",
    }
    state["last_token"] = "identifier" if member else word
    return end


def _javascript_number(content: str, start: int, state: Dict[str, Any]) -> int:
    end = start + 1
    while end < len(content) and (content[end].isalnum() or content[end] in "_."):
        end += 1
    state["allow_regex"] = False
    state["last_token"] = "number"
    return end


def _javascript_punctuation(content: str, index: int, state: Dict[str, Any]) -> int:
    char = content[index]
    if char == "(":
        control = state["last_token"] in {"if", "while", "for", "with", "switch", "catch"}
        state["parens"].append((control, state["pending_function"]))
        state["pending_function"] = None
        state["allow_regex"] = True
    elif char == ")":
        control, function = state["parens"].pop() if state["parens"] else (False, None)
        state["allow_regex"] = control
        if function is not None:
            state["function_body"] = function
    elif char == "{":
        if state["function_body"] is not None:
            expression = state["function_body"]
            state["function_body"] = None
        elif state["pending_class"] is not None:
            expression = state["pending_class"]
            state["pending_class"] = None
        else:
            expression = state["last_token"] in state["expression_prefixes"]
        state["braces"].append(expression)
        state["allow_regex"] = True
    elif char == "}":
        state["allow_regex"] = not (state["braces"].pop() if state["braces"] else False)
        state["restricted"] = False
    elif char == "]":
        state["allow_regex"] = False
    elif char == ".":
        state["allow_regex"] = content.startswith("...", index)
    elif char in "+-" and content.startswith(char * 2, index):
        state["last_token"] = char * 2
        return index + 2
    elif not char.isspace():
        state["allow_regex"] = True
    if char.isspace():
        return index + 1
    if char == ";":
        state["restricted"] = False
    if content.startswith("=>", index):
        state["last_token"] = "=>"
        return index + 2
    state["last_token"] = char
    return index + 1


def _javascript_step(content: str, index: int, state: Dict[str, Any]) -> int:
    char = content[index]
    if char.isalpha() or char in "_$":
        return _javascript_identifier(content, index, state)
    if char.isdigit():
        return _javascript_number(content, index, state)
    return _javascript_punctuation(content, index, state)


def _comment_end(content: str, start: int, language: str) -> int:
    if content.startswith("//", start):
        end = content.find("\n", start)
        return len(content) if end < 0 else end
    if not content.startswith("/*", start):
        return -1
    depth = 1
    index = start + 2
    while index < len(content) and depth:
        if language == "rust" and content.startswith("/*", index):
            depth += 1
            index += 2
        elif content.startswith("*/", index):
            depth -= 1
            index += 2
        else:
            index += 1
    return index


def _literal_end(content: str, start: int, language: str) -> Tuple[int, str]:
    size = len(content)
    if language == "rust" and content[start] in "br":
        raw = re.match(r'(?:br|r)(#*)"', content[start:])
        if raw:
            delimiter = '"' + raw.group(1)
            end = content.find(delimiter, start + raw.end())
            return (size if end < 0 else end + len(delimiter)), "string"
    quote = content[start]
    if quote not in ('"', "'", "`"):
        return -1, ""
    if language == "rust" and quote == "'":
        char = re.match(r"'(?:\\.|[^'\\\n])'", content[start:])
        return (start + char.end(), "string") if char else (-1, "")
    index = start + 1
    while index < size:
        if content[index] == "\\" and not (language == "go" and quote == "`"):
            index += 2
        elif content[index] == quote:
            return index + 1, "string"
        else:
            index += 1
    return min(index, size), "string"


def noncode_spans(content: str, language: str) -> Iterator[Tuple[int, int, str]]:
    """Yield comments and literals without interpreting markers inside strings."""
    i = 0
    size = len(content)
    javascript = language in ("javascript", "typescript")
    state = {
        "allow_regex": True, "last_token": "", "parens": [], "braces": [],
        "pending_function": None, "pending_class": None, "function_body": None,
        "restricted": False, "expression_prefixes": {"=", "(", "[", ",", ":", "return", "yield", "=>"},
    }
    while i < size:
        start = i
        if javascript and state["restricted"] and content[i] in "\r\n\u2028\u2029":
            # A line terminator inserts a semicolon after break/continue,
            # including the labelled forms of those statements.
            state["allow_regex"] = True
            state["last_token"] = ""
            state["restricted"] = False
        comment_end = _comment_end(content, i, language)
        if comment_end >= 0:
            i = comment_end
            if javascript and state["restricted"] and content.startswith("/*", start) and any(char in content[start:i] for char in "\r\n\u2028\u2029"):
                state["allow_regex"] = True
                state["last_token"] = ""
                state["restricted"] = False
            yield start, i, "comment"
            continue
        if javascript and content[i] == "/" and state["allow_regex"]:
            end = _regex_end(content, i)
            if end >= 0:
                i = end
                state["allow_regex"] = False
                state["last_token"] = "literal"
                yield start, i, "regex"
                continue
        literal_end, kind = _literal_end(content, i, language)
        if literal_end >= 0:
            if kind == "string" and language == "rust" and content[i] == "'" and literal_end == i:
                i += 1
                continue
            i = literal_end
            yield start, i, kind
            if javascript:
                state["allow_regex"] = False
                state["last_token"] = "literal"
            continue
        if javascript:
            i = _javascript_step(content, i, state)
            continue
        i += 1


def mask_noncode(content: str, language: str, mask_strings: bool = True) -> str:
    chars = list(content)
    for start, end, kind in noncode_spans(content, language):
        if kind == "string" and not mask_strings:
            continue
        for i in range(start, end):
            if chars[i] not in "\r\n":
                chars[i] = " "
    return "".join(chars)


def split_parameters(raw: str) -> List[str]:
    """Split commas outside nested generic types, annotations and defaults."""
    parts = []
    start = 0
    depth = 0
    for i, char in enumerate(raw):
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(raw[start:i].strip())
            start = i + 1
    parts.append(raw[start:].strip())
    return [part for part in parts if part]
