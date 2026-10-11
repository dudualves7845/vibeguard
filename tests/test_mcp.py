"""Tests for VibeGuard Model Context Protocol (MCP) server."""

import argparse
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from vibeguard.cli import cmd_mcp
from vibeguard.mcp import (
    MCPServer,
    get_config_snippets,
    install_antigravity_config,
    install_cursor_config,
    run_mcp_server,
)


class TestMCPServer(unittest.TestCase):
    """Comprehensive test suite for MCP JSON-RPC 2.0 stdio server and tools."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)
        (self.root / ".vibeguard.json").write_text(
            json.dumps({
                "project_name": "test_mcp_proj",
                "protected_paths": ["*.py"],
                "ignored_paths": [".git/*", ".vibeguard/*"],
                "max_file_lines": 50,
                "max_cyclomatic_complexity": 5,
                "fail_on_hallucinated_imports": True,
                "fail_on_secret_leak": True,
                "fail_on_dangerous_calls": True,
                "rules": [
                    {
                        "id": "no-forbidden-lib",
                        "description": "Forbidden import test",
                        "rule_type": "forbidden_import",
                        "target_patterns": ["*.py"],
                        "parameters": {"forbidden": ["forbidden_pkg"]},
                        "severity": "error",
                    },
                    {
                        "id": "line-cap-rule",
                        "description": "Rule line limit",
                        "rule_type": "max_lines",
                        "target_patterns": ["*.py"],
                        "parameters": {"max": 30},
                        "severity": "warning",
                    },
                    {
                        "id": "complexity-rule",
                        "description": "Rule complexity limit",
                        "rule_type": "max_complexity",
                        "target_patterns": ["*.py"],
                        "parameters": {"max": 3},
                        "severity": "error",
                    },
                ],
            }),
            encoding="utf-8",
        )
        (self.root / "module.py").write_text(
            "def calculate_total(a, b):\n    return a + b\n\ndef exported_api():\n    return 'ok'\n",
            encoding="utf-8",
        )
        self.server = MCPServer(project_path=str(self.root))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _pipe_exchange(self, raw_input):
        in_stream = io.StringIO(raw_input)
        out_stream = io.StringIO()
        self.server.run_loop(in_stream, out_stream)
        raw_output = out_stream.getvalue().strip()
        lines = [ln for ln in raw_output.split("\n") if ln.strip()]
        return [json.loads(ln) for ln in lines]

    def test_initialize_handshake(self):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "cursor", "version": "1.0"},
            },
        }
        res = self.server.handle_message(json.dumps(req))
        self.assertEqual(res["id"], 1)
        self.assertEqual(res["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(res["result"]["serverInfo"]["name"], "vibeguard")
        self.assertIn("tools", res["result"]["capabilities"])

    def test_notifications_and_ping(self):
        # Initialized notification (no response expected)
        notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        res = self.server.handle_message(json.dumps(notify))
        self.assertIsNone(res)

        # Initialized request with id
        init_req = {"jsonrpc": "2.0", "id": 2, "method": "notifications/initialized"}
        res = self.server.handle_message(json.dumps(init_req))
        self.assertEqual(res["id"], 2)
        self.assertEqual(res["result"], {})

        # Ping
        ping_req = {"jsonrpc": "2.0", "id": 3, "method": "ping"}
        res = self.server.handle_message(json.dumps(ping_req))
        self.assertEqual(res["id"], 3)
        self.assertEqual(res["result"], {})

        # Discovery of resources and prompts (IDE probing compatibility)
        res_res = self.server.handle_message(json.dumps({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}))
        self.assertEqual(res_res["result"], {"resources": []})
        res_prm = self.server.handle_message(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"}))
        self.assertEqual(res_prm["result"], {"prompts": []})

    def test_list_tools(self):
        req = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
        res = self.server.handle_message(json.dumps(req))
        tools = res["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        expected = {
            "vibeguard_check",
            "vibeguard_validate_code",
            "vibeguard_get_contracts",
            "vibeguard_get_invariants",
            "vibeguard_baseline",
            "vibeguard_remediate",
            "vibeguard_checkpoint",
            "vibeguard_rewind",
        }
        self.assertEqual(tool_names, expected)


    def test_tool_baseline_and_get_contracts(self):
        # 1. Capture baseline
        res_b = self.server.call_tool("vibeguard_baseline", {"project_root": str(self.root)})
        self.assertTrue(res_b["success"])
        self.assertGreaterEqual(res_b["total_files"], 1)

        # 2. Get contracts from baseline using relative and absolute paths
        res_c = self.server.call_tool(
            "vibeguard_get_contracts",
            {"project_root": str(self.root), "from_baseline": True},
        )
        self.assertIn("module.py", res_c["contracts"])
        symbols = [s["name"] for s in res_c["contracts"]["module.py"]["symbols"]]
        self.assertIn("calculate_total", symbols)
        self.assertIn("exported_api", symbols)

        # Absolute path lookup from baseline
        abs_file = str(self.root / "module.py")
        res_abs = self.server.call_tool(
            "vibeguard_get_contracts",
            {"project_root": str(self.root), "file_path": abs_file, "from_baseline": True},
        )
        self.assertIn("module.py", res_abs["contracts"])

        # 3. Get contracts for single file from disk (relative and absolute)
        res_single = self.server.call_tool(
            "vibeguard_get_contracts",
            {"project_root": str(self.root), "file_path": "module.py"},
        )
        self.assertIn("module.py", res_single["contracts"])

        res_single_abs = self.server.call_tool(
            "vibeguard_get_contracts",
            {"project_root": str(self.root), "file_path": abs_file},
        )
        self.assertIn("module.py", res_single_abs["contracts"])

        # Non-existent file does not crash and returns empty contracts
        res_ghost = self.server.call_tool(
            "vibeguard_get_contracts",
            {"project_root": str(self.root), "file_path": "phantom.py"},
        )
        self.assertEqual(res_ghost["total_files"], 0)

    def test_tool_get_invariants(self):
        res = self.server.call_tool("vibeguard_get_invariants", {"project_root": str(self.root)})
        self.assertEqual(res["project_name"], "test_mcp_proj")
        self.assertEqual(res["max_file_lines"], 50)
        self.assertTrue(res["fail_on_hallucinated_imports"])
        self.assertEqual(len(res["rules"]), 3)

    def test_tool_check_variants(self):
        # 1. Full check
        res = self.server.call_tool("vibeguard_check", {"project_root": str(self.root)})
        self.assertIn("total_findings", res)
        self.assertIn("summary", res)
        self.assertIn("ai_prompt", res)
        self.assertFalse(res["has_blocking_errors"])

        # 2. Targeted check with absolute path
        abs_file = str(self.root / "module.py")
        res_files = self.server.call_tool("vibeguard_check", {
            "project_root": str(self.root),
            "files": [abs_file],
        })
        self.assertEqual(res_files["total_findings"], 0)

        # 3. Targeted check with single string path
        res_str = self.server.call_tool("vibeguard_check", {
            "project_root": str(self.root),
            "files": "module.py",
        })
        self.assertEqual(res_str["total_findings"], 0)

        # 4. Staged and diff modes on non-git directory report clear error
        res_staged = self.server.call_tool("vibeguard_check", {
            "project_root": str(self.root),
            "staged": True,
        })
        self.assertTrue(res_staged["has_blocking_errors"])
        self.assertIn("Not a git repo", res_staged["summary"])

    def test_tool_validate_code_clean(self):
        clean_code = "def calculate_total(a, b):\n    return a + b\n\ndef exported_api():\n    return 'ok'\n"
        res = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": "module.py",
            "content": clean_code,
        })
        self.assertTrue(res["valid"])
        self.assertEqual(res["blocking_count"], 0)

    def test_tool_validate_code_catches_deleted_contract(self):
        self.server.call_tool("vibeguard_baseline", {"project_root": str(self.root)})
        broken_code = "def calculate_total(a, b):\n    return a + b\n"
        res = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": "module.py",
            "content": broken_code,
        })
        self.assertFalse(res["valid"])
        self.assertGreaterEqual(res["blocking_count"], 1)
        cats = [f["category"] for f in res["findings"]]
        self.assertIn("api_breakage", cats)

    def test_tool_validate_code_disk_fallback_without_baseline(self):
        # No baseline captured yet; checks contracts against disk file directly
        broken_code = "def calculate_total(a, b):\n    return a + b\n"
        res = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": str(self.root / "module.py"),
            "content": broken_code,
        })
        self.assertFalse(res["valid"])
        cats = [f["category"] for f in res["findings"]]
        self.assertIn("api_breakage", cats)

    def test_tool_validate_code_catches_signature_change(self):
        self.server.call_tool("vibeguard_baseline", {"project_root": str(self.root)})
        sig_changed = "def calculate_total(a, b, extra):\n    return a + b + extra\n\ndef exported_api():\n    return 'ok'\n"
        res = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": "module.py",
            "content": sig_changed,
        })
        cats = [f["category"] for f in res["findings"]]
        self.assertIn("api_breakage", cats)

    def test_tool_validate_code_catches_hallucinated_import(self):
        hallucinated_code = "import nonexistent_phantom_package_xyz\ndef calculate_total(a, b):\n    return a + b\n"
        res = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": "module.py",
            "content": hallucinated_code,
        })
        self.assertFalse(res["valid"])
        cats = [f["category"] for f in res["findings"]]
        self.assertIn("hallucinated_import", cats)

    def test_tool_validate_code_catches_empty_stub_and_secret(self):
        stripe_key = "sk_" + "live_" + "1234567890abcdef1234567890"
        bad_code = f"def calculate_total(a, b):\n    pass\n\nAKEY = '{stripe_key}'\n"
        res = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": "module.py",
            "content": bad_code,
        })
        self.assertFalse(res["valid"])
        cats = [f["category"] for f in res["findings"]]
        self.assertIn("stub_injected", cats)
        self.assertIn("secret_leak", cats)

    def test_tool_validate_code_catches_rules_and_complexity(self):
        # 1. Forbidden import
        forbidden_code = "import forbidden_pkg\ndef calculate_total(a, b):\n    return a + b\n"
        res_f = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": "module.py",
            "content": forbidden_code,
        })
        self.assertFalse(res_f["valid"])
        self.assertIn("rule_violation", [f["category"] for f in res_f["findings"]])

        # 2. Max lines rule
        bloated_code = "def calculate_total(a, b):\n" + "\n".join(f"    x_{i} = {i}" for i in range(40)) + "\n    return a + b\n"
        res_b = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": "module.py",
            "content": bloated_code,
        })
        self.assertIn("rule_violation", [f["category"] for f in res_b["findings"]])

        # 3. Dangerous call and high cyclomatic complexity
        complex_code = """def calculate_total(a, b):
    eval('2 + 2')
    if a:
        if b:
            if a > b:
                return a
    return b
"""
        res_c = self.server.call_tool("vibeguard_validate_code", {
            "project_root": str(self.root),
            "file_path": "module.py",
            "content": complex_code,
        })
        cats = [f["category"] for f in res_c["findings"]]
        self.assertIn("dangerous_call", cats)
        self.assertIn("rule_violation", cats)

    def test_tool_remediate(self):
        self.server.call_tool("vibeguard_baseline", {"project_root": str(self.root)})
        (self.root / "module.py").write_text("def calculate_total(a, b):\n    return a + b\n", encoding="utf-8")

        res = self.server.call_tool("vibeguard_remediate", {"project_root": str(self.root)})
        self.assertIn("remediation_prompt", res)
        self.assertIn("git_patch", res)
        self.assertGreater(res["total_findings"], 0)
        self.assertIn("--- a/module.py", res["git_patch"])
        self.assertIn("+++ b/module.py", res["git_patch"])

    def test_pipe_simulation_single_line_and_multiline(self):
        # 1. Normal newline-delimited stream
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "vibeguard_get_invariants", "arguments": {"project_root": str(self.root)}},
            },
        ]
        raw_stream = "\n".join(json.dumps(m) for m in messages) + "\n"
        responses = self._pipe_exchange(raw_stream)
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[0]["id"], 1)
        self.assertEqual(responses[1]["id"], 2)
        self.assertEqual(responses[2]["id"], 3)
        self.assertFalse(responses[2]["result"]["isError"])

        # 2. Concatenated JSON messages on a single line (no newlines between them)
        concatenated = json.dumps({"jsonrpc": "2.0", "id": 10, "method": "ping"}) + json.dumps({"jsonrpc": "2.0", "id": 11, "method": "ping"}) + "\n"
        res_concat = self._pipe_exchange(concatenated)
        self.assertEqual(len(res_concat), 2)
        self.assertEqual(res_concat[0]["id"], 10)
        self.assertEqual(res_concat[1]["id"], 11)

        # 3. Multiline pretty-printed JSON message
        pretty_req = json.dumps({"jsonrpc": "2.0", "id": 20, "method": "ping"}, indent=2) + "\n"
        res_pretty = self._pipe_exchange(pretty_req)
        self.assertEqual(len(res_pretty), 1)
        self.assertEqual(res_pretty[0]["id"], 20)

    def test_error_handling(self):
        # Parse error
        resp = self.server.handle_message("{malformed json")
        self.assertEqual(resp["error"]["code"], -32700)

        # Pipe malformed input returns parse error
        pipe_err = self._pipe_exchange("not valid json at all\n")
        self.assertEqual(pipe_err[0]["error"]["code"], -32700)

        # Invalid Request (non-dict)
        resp = self.server.handle_message(json.dumps(["not a dict"]))
        self.assertEqual(resp["error"]["code"], -32600)

        # Method not a string
        resp = self.server.handle_message(json.dumps({"jsonrpc": "2.0", "id": 12, "method": 12345}))
        self.assertEqual(resp["error"]["code"], -32600)

        # Method not found
        resp = self.server.handle_message(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "non_existent_rpc"}))
        self.assertEqual(resp["error"]["code"], -32601)

        # Unknown tool execution
        resp = self.server.handle_message(json.dumps({
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {"name": "ghost_tool", "arguments": {}},
        }))
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("Unknown tool", resp["result"]["content"][0]["text"])

        # Null params or null arguments in tools/call does not crash
        resp_null = self.server.handle_message(json.dumps({
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {"name": "vibeguard_get_invariants", "arguments": None},
        }))
        self.assertFalse(resp_null["result"]["isError"])

    def test_cli_config_snippets_and_installers(self):
        snippets = get_config_snippets()
        self.assertIn("Cursor", snippets)
        self.assertIn("Claude Desktop", snippets)
        self.assertIn("Antigravity", snippets)
        self.assertIn("vibeguard", snippets)

        # Install Cursor config
        cursor_file = install_cursor_config(target_dir=self.root)
        self.assertTrue(cursor_file.exists())
        data = json.loads(cursor_file.read_text(encoding="utf-8"))
        self.assertIn("vibeguard", data["mcpServers"])

        # Install Antigravity config
        ag_target = self.root / "gemini" / "mcp_config.json"
        ag_file = install_antigravity_config(config_path=ag_target)
        self.assertTrue(ag_file.exists())
        data_ag = json.loads(ag_file.read_text(encoding="utf-8"))
        self.assertIn("vibeguard", data_ag["mcpServers"])

    def test_cli_subcommand_dispatch(self):
        # Test cmd_mcp with --config-snippet
        args = argparse.Namespace(config_snippet=True, install_cursor=False, install_antigravity=False, project=None)
        self.assertEqual(cmd_mcp(args), 0)

        # Test cmd_mcp with --install-cursor targeting specific project dir
        custom_target = self.root / "subproject"
        custom_target.mkdir()
        args_inst = argparse.Namespace(config_snippet=False, install_cursor=True, install_antigravity=False, project=str(custom_target))
        self.assertEqual(cmd_mcp(args_inst), 0)
        self.assertTrue((custom_target / ".cursor" / "mcp.json").exists())

    def test_run_mcp_server_entry(self):
        in_s = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
        out_s = io.StringIO()
        code = run_mcp_server(in_stream=in_s, out_stream=out_s, project_path=str(self.root))
        self.assertEqual(code, 0)
        resp = json.loads(out_s.getvalue().strip())
        self.assertEqual(resp["id"], 1)


if __name__ == "__main__":
    unittest.main()
