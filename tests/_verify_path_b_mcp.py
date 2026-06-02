"""Path B verification — spawn report-skill-mcp, complete the MCP
initialize handshake, then call the `report_create` tool with a minimal
draft. Proves that any MCP client (Claude Desktop, Continue, Cursor, etc.)
given just the server URL via env can post documents."""
from __future__ import annotations
import json
import subprocess
import sys
import time

EXE = r"d:\report-skill\venv\Scripts\report-skill-mcp.exe"


def send(p, msg):
    p.stdin.write((json.dumps(msg) + "\n").encode())
    p.stdin.flush()


def recv(p, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = p.stdout.readline()
        if line:
            return json.loads(line.decode())
        if p.poll() is not None:
            err = p.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"server exited rc={p.returncode}\n{err}")
    raise TimeoutError("no message")


def main():
    proc = subprocess.Popen(
        [EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        # 1) initialize
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "capabilities": {},
                               "clientInfo": {"name": "path-b-verify", "version": "0"}}})
        init = recv(proc)
        print("initialize:", init.get("result", {}).get("serverInfo"))
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 2) call report_create with a tiny draft
        args = {
            "template_id": "weekly-dev",
            "blocks": {
                "meta": {"sprint": "verify-mcp"},
                "summary": "Posted via MCP report_create tool — Path B verification.",
                "progress": ["MCP stdio handshake", "tools/call report_create"],
                "next_week": ["delete this report"],
            },
            "title": "[verify-mcp] LLM via MCP server",
            "report_date": "2026-05-29",
            "tags": ["verify", "mcp"],
        }
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "report_create", "arguments": args}})
        resp = recv(proc, timeout=30.0)
        content = resp.get("result", {}).get("content", [])
        if not content or content[0].get("type") != "text":
            print("UNEXPECTED:", json.dumps(resp, ensure_ascii=False)[:500])
            return 1
        payload = json.loads(content[0]["text"])
        print("report_create response keys:", sorted(payload))
        print(f"PATH B reportId: {payload.get('id')}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
