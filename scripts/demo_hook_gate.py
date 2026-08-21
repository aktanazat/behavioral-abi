#!/usr/bin/env python3
"""Demonstrate a verified contract blocking a retry before execution."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp.client import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.nonidempotent_server import CLIENT_INFO, Record, build_server


async def run() -> int:
    contract = json.loads(Path("runs/deny-demo/contract.json").read_text())
    tool_name = contract["predicate"]["operation"]["name"]
    state: list[Record] = []
    async with Client(server=build_server(state), mode="legacy", client_info=CLIENT_INFO) as client:
        first = await client.call_tool(tool_name, {"name": "babi-hook-demo"})
        if first.is_error:
            raise RuntimeError("first server call failed")

        event = {"tool_name": tool_name, "tool_input": {"name": "babi-hook-demo"}}
        environment = dict(os.environ, BABI_HOOK_CONFIG=str(Path("runs/hook-config.json").resolve()))
        hook = subprocess.run(
            [sys.executable, "hooks/pre_tool_use.py"],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            env=environment,
            timeout=10,
            check=True,
        )
        decision = json.loads(hook.stdout)["hookSpecificOutput"]["permissionDecision"]
        if decision != "deny":
            await client.call_tool(tool_name, {"name": "babi-hook-demo"})

    print(f"first_call_count=1")
    print(f"hook_decision={decision}")
    print(f"server_state_count={len(state)}")
    print(f"second_call_reached_server={str(len(state) > 1).lower()}")
    return 0 if decision == "deny" and len(state) == 1 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
