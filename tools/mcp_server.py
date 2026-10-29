#!/usr/bin/env python3
"""Read-only MCP server for the reservoir harness.

Stdio, Content-Length framing, standard library only. Tools list skills, run
doctor, and return workflows. This process never starts a simulator, queue job,
upload, delete, or shell.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "2024-11-05"
MAX_BODY = 1_000_000
INSTRUCTIONS = (
    "Read-only reservoir harness. Use list_skills, doctor, and show_workflow. "
    "This server never starts ERT, a simulator, LSF, an upload, or a delete."
)

WORKFLOWS = {
    "linux-direct": (
        "The agent host is Linux. Do not use the Windows handoff.\n"
        "1. Load the skill reservoir-engineer and pick the task skill.\n"
        "2. Call list_skills if the task skill is unclear.\n"
        "3. Call doctor for preflight. site_verified is always false.\n"
        "4. Case QC writes only to a new empty output directory. It does not submit a run."
    ),
    "case-qc": (
        "Prepare one case_qc.py command. Do not launch ERT or a simulator.\n"
        "Use the contract Python, a quoted runpath glob, an expected member set, "
        "and a new empty outdir under output_root. "
        "A missing key, member, or nonfinite value is not success."
    ),
}


def _skills() -> list[dict[str, str]]:
    found = []
    root = ROOT / "skills"
    if not root.is_dir():
        return found
    for path in sorted(root.glob("*/SKILL.md")):
        name = path.parent.name
        description = ""
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            block = text[3:end] if end != -1 else ""
            for line in block.splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("\"'")
        found.append({"name": name, "description": description})
    return found


def _text(payload: object, *, error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "isError": error,
    }


def _tool_schemas() -> list[dict]:
    return [
        {
            "name": "list_skills",
            "description": "List reservoir skills in this checkout. Read-only.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "doctor",
            "description": "Run local doctor preflight. Does not start jobs or contact services.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["agent", "execution"]},
                    "contract": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "show_workflow",
            "description": "Return the linux-direct or case-qc workflow.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string", "enum": sorted(WORKFLOWS)}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    ]


def _doctor(arguments: dict) -> dict:
    mode = arguments.get("mode", "agent")
    if mode not in {"agent", "execution"}:
        return _text({"error": "mode must be agent or execution"}, error=True)
    argv = [sys.executable, str(ROOT / "tools" / "doctor.py"), "--mode", mode, "--json"]
    contract = arguments.get("contract")
    if contract is not None:
        if not isinstance(contract, str) or not contract.strip() or "\x00" in contract:
            return _text({"error": "contract must be a path"}, error=True)
        argv.extend(["--contract", contract])
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        report = {"ready": False, "stdout": completed.stdout, "stderr": completed.stderr}
    report["exit_code"] = completed.returncode
    return _text(report, error=completed.returncode != 0)


def _call(name: str, arguments: object) -> dict:
    if name in {"shell", "exec", "run", "run_ert", "upload", "delete", "bash"}:
        return _text({"error": f"{name} is not a tool. Use the shell."}, error=True)
    if not isinstance(arguments, dict):
        return _text({"error": "arguments must be an object"}, error=True)
    allowed = {
        "list_skills": set(),
        "doctor": {"mode", "contract"},
        "show_workflow": {"name"},
    }
    if name not in allowed:
        return _text({"error": f"unknown tool: {name}"}, error=True)
    extra = set(arguments) - allowed[name]
    if extra:
        return _text({"error": f"unexpected arguments: {sorted(extra)}"}, error=True)
    if name == "list_skills":
        return _text({"skills": _skills()})
    if name == "doctor":
        return _doctor(arguments)
    workflow = WORKFLOWS.get(str(arguments.get("name")))
    if workflow is None:
        return _text({"error": "unknown workflow"}, error=True)
    return _text({"name": arguments["name"], "steps": workflow})


def handle(message: dict) -> dict | None:
    """Return a JSON-RPC response, or None for a notification."""
    if not isinstance(message, dict):
        return None
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    raw_params = message.get("params")
    params: dict = raw_params if isinstance(raw_params, dict) else {}
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": "reservoir", "version": "1"},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _tool_schemas()}
    elif method == "tools/call":
        result = _call(str(params.get("name") or ""), params.get("arguments") or {})
    elif method == "prompts/list":
        result = {
            "prompts": [
                {
                    "name": name,
                    "description": f"Reservoir workflow: {name}",
                }
                for name in sorted(WORKFLOWS)
            ]
        }
    elif method == "prompts/get":
        name = str(params.get("name") or "")
        if name not in WORKFLOWS:
            return _error(request_id, -32602, "unknown prompt")
        result = {
            "description": name,
            "messages": [{"role": "user", "content": {"type": "text", "text": WORKFLOWS[name]}}],
        }
    else:
        return _error(request_id, -32601, "method not found")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def encode(message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def read_message(stream) -> dict | None:
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = stream.read(1)
        if not chunk:
            return None
        header += chunk
        if len(header) > 8192:
            raise ValueError("header too large")
    raw, _, pending = header.partition(b"\r\n\r\n")
    length = None
    for line in raw.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    if length is None or length < 2 or length > MAX_BODY:
        raise ValueError("invalid Content-Length")
    body = pending
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body.decode())


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        try:
            message = read_message(stdin)
        except (ValueError, json.JSONDecodeError) as error:
            print(f"mcp_server: {error}", file=sys.stderr)
            return 1
        if message is None:
            return 0
        response = handle(message)
        if response is not None:
            stdout.write(encode(response))
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
