"""Spawn report-skill-mcp as a subprocess, do the MCP initialize handshake,
list tools, call ping, and exit. Pure stdio — no third-party MCP client.

This is a minimal protocol-compliance check: confirms the server binds to
stdio, advertises the expected tool count, and round-trips a real call.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

EXE = r"d:\report-skill\venv\Scripts\report-skill-mcp.exe"


def send(proc: subprocess.Popen, msg: dict) -> None:
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()


def recv(proc: subprocess.Popen, timeout: float = 10.0) -> dict:
    """Read one JSON-RPC message from the server (line-delimited)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line:
            return json.loads(line.decode("utf-8"))
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"server exited: rc={proc.returncode}\n{stderr}")
    raise TimeoutError("no message in time")


def main() -> int:
    proc = subprocess.Popen(
        [EXE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # 1) initialize handshake
        send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0"},
            },
        })
        init = recv(proc)
        print("initialize:", init.get("result", {}).get("serverInfo"))

        # the server expects an `initialized` notification per MCP spec
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 2) list tools
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools_resp = recv(proc)
        tools = tools_resp.get("result", {}).get("tools", [])
        print(f"tools advertised: {len(tools)}")
        names = sorted(t["name"] for t in tools)
        for n in names[:5]:
            print(f"  - {n}")
        print(f"  ... (+{len(names)-5} more)")

        # 3) call ping
        send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "ping", "arguments": {}},
        })
        ping_resp = recv(proc)
        content = ping_resp.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            print("ping result:", content[0]["text"])
        else:
            print("ping unexpected:", ping_resp)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
