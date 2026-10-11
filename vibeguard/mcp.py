"""Native zero-dependency Model Context Protocol (MCP) server for VibeGuard.

Exposes JSON-RPC 2.0 stdio server for AI tools (Cursor, Claude, Antigravity, Windsurf).
"""

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, TextIO

from .analyzer import CodeAnalyzer
from .config import GuardConfig
from .drift import DriftEngine
from .git_utils import GitIntegration
from .remediation import RemediationGenerator
from .snapshot import SnapshotEngine, ProjectSnapshot
from .evaluation import evaluate_project


def _tool_def(name: str, desc: str, props: Dict[str, Any], req: Optional[List[str]] = None) -> Dict[str, Any]:
    s: Dict[str, Any] = {"type": "object", "properties": props}
    if req:
        s["required"] = req
    return {"name": name, "description": desc, "inputSchema": s}


def _norm_path(p: str, root: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(root))
    except Exception:
        return os.path.relpath(p, str(root)) if os.path.isabs(p) else str(p)


def _norm_files(raw: Any, root: Path) -> Optional[List[str]]:
    return [_norm_path(f, root) for f in ([raw] if isinstance(raw, str) else list(raw)) if f] if raw else None


def _finding(cat: str, sev: str, title: str, msg: str, remedy: str, line: Optional[int] = None, file_path: Optional[str] = None) -> Dict[str, Any]:
    res = {"category": cat, "severity": sev, "line_number": line, "line": line, "title": title, "message": msg, "remediation": remedy}
    if file_path:
        res["file_path"] = file_path
    return res


class MCPServer:
    """Zero-dependency MCP stdio server implementation."""

    def __init__(self, project_path: Optional[str] = None):
        self.project_path = Path(project_path).resolve() if project_path else Path.cwd().resolve()

    def list_tools(self) -> List[Dict[str, Any]]:
        opt_root = {"project_root": {"type": "string", "description": "Project root directory (optional)"}}
        return [
            _tool_def("vibeguard_check", "Runs drift and invariant check on the project or files (supports git staged or diff modes).", {
                **opt_root, "files": {"type": "array", "items": {"type": "string"}, "description": "Specific files to check"},
                "staged": {"type": "boolean", "description": "Only check git staged files"}, "diff": {"type": "boolean", "description": "Only check modified files relative to git HEAD"},
            }),
            _tool_def("vibeguard_validate_code", "Validates proposed code before writing against invariants (contracts, imports, stubs, secrets).", {
                **opt_root, "file_path": {"type": "string", "description": "Target file path to validate"}, "content": {"type": "string", "description": "Proposed file content to validate"},
            }, ["file_path", "content"]),
            _tool_def("vibeguard_get_contracts", "Returns protected symbol signatures and parameters for a file or workspace.", {
                **opt_root, "file_path": {"type": "string", "description": "Optional specific file path"}, "from_baseline": {"type": "boolean", "description": "If true, load from saved baseline snapshot"},
            }),
            _tool_def("vibeguard_get_invariants", "Returns configured architectural rules from .vibeguard.json.", opt_root),
            _tool_def("vibeguard_baseline", "Captures or updates the architectural baseline snapshot of the project.", opt_root),
            _tool_def("vibeguard_remediate", "Generates remediation guidance and a patch with annotations; it does not restore missing implementations.", {
                **opt_root, "target_files": {"type": "array", "items": {"type": "string"}, "description": "Target files"}, "files": {"type": "array", "items": {"type": "string"}, "description": "Alias for target_files"},
            }),
            _tool_def("vibeguard_checkpoint", "Captures working-tree files and Git index state before risky edits.", {
                **opt_root, "message": {"type": "string", "description": "Checkpoint description"},
            }),
            _tool_def("vibeguard_rewind", "Safely rewinds workspace to a checkpoint or steps back.", {
                **opt_root, "checkpoint_id": {"type": "string", "description": "Specific checkpoint ID to restore"}, "step": {"type": "integer", "description": "Steps back (default: 1)"},
            }),
        ]

    def call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        definition = next((tool for tool in self.list_tools() if tool["name"] == name), None)
        if definition is None:
            raise ValueError(f"Unknown tool: '{name}'")
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        args = dict(args)
        # Preserve the existing convenience alias accepting a single file path.
        for key in ("files", "target_files"):
            if isinstance(args.get(key), str):
                args[key] = [args[key]]
        schema = definition["inputSchema"]
        for required in schema.get("required", []):
            if required not in args:
                raise ValueError(f"Missing required argument: {required}")
        types = {"string": str, "boolean": bool, "integer": int, "array": list}
        for key, value in args.items():
            specification = schema["properties"].get(key)
            if not specification:
                continue
            expected = specification.get("type")
            if expected in types and (not isinstance(value, types[expected]) or
                                      expected == "integer" and isinstance(value, bool)):
                raise ValueError(f"Argument '{key}' must be {expected}")
            if expected == "array" and not all(isinstance(item, str) for item in value):
                raise ValueError(f"Argument '{key}' must contain strings")
        handlers = {
            "vibeguard_check": self._tool_check, "vibeguard_validate_code": self._tool_validate_code,
            "vibeguard_get_contracts": self._tool_get_contracts, "vibeguard_get_invariants": self._tool_get_invariants,
            "vibeguard_baseline": self._tool_baseline, "vibeguard_remediate": self._tool_remediate,
            "vibeguard_checkpoint": self._tool_checkpoint, "vibeguard_rewind": self._tool_rewind,
        }
        handler = handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: '{name}'")
        return handler(args)


    def _tool_check(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(args.get("project_root") or self.project_path).resolve()
        raw_files, git_helper = args.get("files") or args.get("target_files"), GitIntegration(str(root))
        target_files = None
        if args.get("staged") or args.get("diff"):
            flag = "staged" if args.get("staged") else "diff"
            if not git_helper.is_git_repo():
                return {"total_findings": 0, "critical_count": 0, "error_count": 1, "warning_count": 0, "findings": [_finding("git_error", "error", "Not a git repo", f"Cannot check {flag} outside git.", "Run git init.", None, str(root))], "has_blocking_errors": True, "summary": "Not a git repo.", "message": f"Cannot check {flag} outside git."}
            files = git_helper.get_staged_files() if args.get("staged") else git_helper.get_changed_files()
            if not files:
                msg = f"No {flag} files found in git." if flag == "staged" else "Working tree clean. No changed files to check."
                return {"total_findings": 0, "critical_count": 0, "error_count": 0, "warning_count": 0, "findings": [], "has_blocking_errors": False, "summary": msg, "message": msg, "ai_prompt": f"✅ {msg}"}
            target_files = files
        elif raw_files:
            target_files = _norm_files(raw_files, root)

        report, baseline = evaluate_project(root, staged=bool(args.get("staged")),
                                            diff=bool(args.get("diff")), target_files=target_files)
        res = report.to_dict()
        res["summary"] = RemediationGenerator.generate_summary(report)
        res["ai_prompt"] = RemediationGenerator.generate_ai_prompt(report)
        return res

    def _tool_validate_code(self, args: Dict[str, Any]) -> Dict[str, Any]:
        file_path, content = args.get("file_path") or "", args.get("content") or ""
        root = Path(args.get("project_root") or self.project_path).resolve()
        rel_path = _norm_path(file_path, root) if file_path else "snippet"

        config = GuardConfig.load_from_file(str(root / ".vibeguard.json"))
        engine = SnapshotEngine(str(root), config)
        analysis = CodeAnalyzer.analyze_file(rel_path, content)
        baseline = engine.load_baseline()
        if (baseline is None or rel_path not in baseline.files) and (root / rel_path).is_file():
            disk = CodeAnalyzer.analyze_file(rel_path, (root / rel_path).read_text(encoding="utf-8", errors="replace"))
            files = dict(baseline.files) if baseline else {}
            files[rel_path] = asdict(disk)
            baseline = ProjectSnapshot(timestamp=baseline.timestamp if baseline else 0,
                                       total_files=len(files), files=files)
        current = ProjectSnapshot(timestamp=0, total_files=1, files={rel_path: asdict(analysis)})
        report = DriftEngine(str(root), config).evaluate_snapshot(
            current, baseline=baseline, target_files=[rel_path])
        findings = report.to_dict()["findings"]

        blocking = [f for f in findings if f["severity"] in ("critical", "error")]
        is_valid = len(blocking) == 0
        summary = f"Validation failed with {len(blocking)} blocking issue(s)." if not is_valid else (f"Validation passed with {len(findings)} non-blocking warning(s)." if findings else "Validation passed! Proposed code satisfies invariants.")
        return {
            "valid": is_valid, "file_path": rel_path, "total_findings": len(findings),
            "blocking_count": len(blocking), "warning_count": len(findings) - len(blocking),
            "findings": findings, "summary": summary,
        }

    def _tool_get_contracts(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(args.get("project_root") or self.project_path).resolve()
        file_path, from_base = args.get("file_path"), bool(args.get("from_baseline", False))
        engine, contracts, rel_path = SnapshotEngine(str(root)), {}, _norm_path(file_path, root) if file_path else None
        if from_base:
            snapshot = engine.load_baseline()
            if snapshot and snapshot.files:
                for p, finfo in snapshot.files.items():
                    if not rel_path or p == rel_path:
                        contracts[p] = {"language": finfo.get("language"), "total_lines": finfo.get("total_lines"), "symbols": finfo.get("symbols", [])}
        else:
            if rel_path:
                target = root / rel_path
                if target.is_file():
                    content = target.read_text(encoding="utf-8", errors="replace")
                    fa = CodeAnalyzer.analyze_file(rel_path, content)
                    contracts[rel_path] = {"language": fa.language, "total_lines": fa.total_lines, "symbols": [{"name": s.name, "kind": s.kind, "line_number": s.line_number, "parameters": s.parameters, "is_exported": s.is_exported, "complexity": s.complexity} for s in fa.symbols]}
            else:
                snapshot = engine.create_snapshot()
                for p, finfo in snapshot.files.items():
                    contracts[p] = {"language": finfo.get("language"), "total_lines": finfo.get("total_lines"), "symbols": finfo.get("symbols", [])}
        return {"total_files": len(contracts), "contracts": contracts}

    def _tool_get_invariants(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(args.get("project_root") or self.project_path).resolve()
        return GuardConfig.load_from_file(str(root / ".vibeguard.json")).to_dict()

    def _tool_baseline(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(args.get("project_root") or self.project_path).resolve()
        engine = SnapshotEngine(str(root))
        snapshot = engine.create_snapshot()
        path = engine.save_baseline(snapshot)
        return {
            "success": True, "baseline_path": str(path), "total_files": snapshot.total_files,
            "timestamp": snapshot.timestamp, "message": f"Baseline snapshot updated ({snapshot.total_files} files captured).",
        }

    def _tool_remediate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(args.get("project_root") or self.project_path).resolve()
        raw_files = args.get("target_files") or args.get("files")
        target_files = _norm_files(raw_files, root)
        engine = DriftEngine(str(root))
        baseline = engine.snapshot_engine.load_baseline()
        report = engine.evaluate(baseline=baseline, target_files=target_files)
        git_patch = RemediationGenerator.generate_git_patch(report, str(root), baseline) if baseline else ""
        return {
            "total_findings": report.total_findings, "has_blocking_errors": report.has_blocking_errors,
            "summary": RemediationGenerator.generate_summary(report),
            "remediation_prompt": RemediationGenerator.generate_ai_prompt(report),
            "git_patch": git_patch, "findings": report.to_dict()["findings"],
        }

    def _tool_checkpoint(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from .rewind import CheckpointManager
        root = Path(args.get("project_root") or self.project_path).resolve()
        cp = CheckpointManager(str(root)).create_checkpoint(message=args.get("message"))
        return {"checkpoint": cp.to_dict(), "message": f"Checkpoint {cp.id} created successfully."}

    def _tool_rewind(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from .rewind import CheckpointManager
        root = Path(args.get("project_root") or self.project_path).resolve()
        to_id, mgr = args.get("checkpoint_id"), CheckpointManager(str(root))
        return mgr.rewind_to(to_id) if to_id else mgr.rewind_step(step=int(args.get("step", 1) or 1))

    def _dispatch_message(self, msg: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(msg, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
        msg_id, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if not isinstance(method, str):
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32600, "message": "Invalid Request: method must be string"}}
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}, "resources": {}, "prompts": {}}, "serverInfo": {"name": "vibeguard", "version": "1.0.0"}}}
        elif method in ("notifications/initialized", "initialized"):
            return None if msg_id is None else {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        elif method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        elif method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": self.list_tools()}}
        elif method in ("resources/list", "prompts/list"):
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources" if method.startswith("res") else "prompts": []}}
        elif method == "tools/call":
            name, args = params.get("name", ""), params.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            try:
                data = self.call_tool(name, args)
                text_content = json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data)
                return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": text_content}], "data": data, "isError": False}}
            except Exception as e:
                return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": f"Error executing tool '{name}': {str(e)}"}], "isError": True}}
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}} if msg_id is not None else None

    def handle_message(self, raw: str) -> Optional[Dict[str, Any]]:
        try:
            msg = json.loads(raw)
        except Exception:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        return self._dispatch_message(msg)

    def run_loop(self, in_stream: TextIO, out_stream: TextIO) -> None:
        buf, decoder = "", json.JSONDecoder()
        while True:
            line = in_stream.readline()
            if not line:
                rem = buf.strip()
                if rem:
                    resp = self.handle_message(rem)
                    if resp is not None:
                        out_stream.write(json.dumps(resp) + "\n")
                        out_stream.flush()
                break
            if buf.strip():
                # Keep legacy pretty-printed input, but resynchronize when a
                # complete new protocol message follows an unfinished one.
                try:
                    next_message = json.loads(line)
                except json.JSONDecodeError:
                    next_message = None
                if isinstance(next_message, dict) and next_message.get("jsonrpc") == "2.0":
                    out_stream.write(json.dumps({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}) + "\n")
                    out_stream.flush()
                    buf = ""
            buf += line
            while buf:
                s = buf.lstrip()
                if not s:
                    buf = ""
                    break
                try:
                    obj, end_idx = decoder.raw_decode(s)
                except json.JSONDecodeError:
                    if not s.startswith(("{", "[")) or s.rstrip().endswith(("}", "]")):
                        resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
                        out_stream.write(json.dumps(resp) + "\n")
                        out_stream.flush()
                        buf = ""
                    break
                else:
                    buf = s[end_idx:]
                    resp = self._dispatch_message(obj)
                    if resp is not None:
                        out_stream.write(json.dumps(resp) + "\n")
                        out_stream.flush()


def get_config_snippets() -> str:
    c = json.dumps({"mcpServers": {"vibeguard": {"command": "vibeguard", "args": ["mcp"]}}}, indent=2)
    return f"=== Cursor Configuration (.cursor/mcp.json) ===\n{c}\n\n=== Claude Desktop Configuration (claude_desktop_config.json) ===\n{c}\n\n=== Antigravity Configuration (mcp_config.json) ===\n{c}"


def _merge_mcp_config(target_file: Path) -> Path:
    target_file.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if target_file.exists():
        try:
            raw = target_file.read_text(encoding="utf-8").strip()
            data = json.loads(raw) if raw and isinstance(json.loads(raw), dict) else {}
        except Exception:
            data = {}
    if not isinstance(data.get("mcpServers"), dict):
        data["mcpServers"] = {}
    data["mcpServers"]["vibeguard"] = {"command": "vibeguard", "args": ["mcp"]}
    target_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target_file


def install_cursor_config(target_dir: Optional[Path] = None) -> Path:
    return _merge_mcp_config((target_dir or Path.cwd()).resolve() / ".cursor" / "mcp.json")


def install_antigravity_config(config_path: Optional[Path] = None) -> Path:
    return _merge_mcp_config(config_path or (Path.home() / ".gemini" / "config" / "mcp_config.json"))


def run_mcp_server(in_stream: Optional[TextIO] = None, out_stream: Optional[TextIO] = None, project_path: Optional[str] = None) -> int:
    MCPServer(project_path=project_path).run_loop(in_stream or sys.stdin, out_stream or sys.stdout)
    return 0

