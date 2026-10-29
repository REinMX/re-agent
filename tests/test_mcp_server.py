"""The reservoir MCP server lists and describes. It does not execute work."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load():
    path = ROOT / "tools/mcp_server.py"
    spec = importlib.util.spec_from_file_location("reservoir_mcp_server", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gate_check_is_not_a_tool():
    module = load()
    response = module.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "gate_check", "arguments": {"command": "ert test_run case.ert"}},
        }
    )
    assert response["result"]["isError"] is True
    assert "unknown tool" in response["result"]["content"][0]["text"]


def test_shell_tool_is_refused():
    module = load()
    response = module.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "shell", "arguments": {"command": "ert test_run case.ert"}},
        }
    )
    assert response["result"]["isError"] is True
    assert "not a tool" in response["result"]["content"][0]["text"]


def test_tools_and_prompts_are_the_closed_set():
    module = load()
    tools = module.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    names = {item["name"] for item in tools["result"]["tools"]}
    assert names == {"list_skills", "doctor", "show_workflow"}
    prompts = module.handle({"jsonrpc": "2.0", "id": 4, "method": "prompts/list"})
    assert {item["name"] for item in prompts["result"]["prompts"]} == {"linux-direct", "case-qc"}


def test_list_skills_includes_the_router():
    module = load()
    response = module.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "list_skills", "arguments": {}},
        }
    )
    names = {item["name"] for item in json.loads(response["result"]["content"][0]["text"])["skills"]}
    assert "reservoir-engineer" in names


def test_stdio_handshake_roundtrip():
    script = ROOT / "tools/mcp_server.py"
    process = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    module = load()
    request = module.encode(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(request)
    process.stdin.flush()
    response = module.read_message(process.stdout)
    process.kill()
    assert response["id"] == 7
    assert response["result"]["serverInfo"]["name"] == "reservoir"
    assert "never starts" in response["result"]["instructions"]
