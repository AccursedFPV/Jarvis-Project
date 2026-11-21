#!/usr/bin/env python3
"""Publish this Jarvis's own tools to the upstream brain over MCP.

The upstream isair/jarvis engine ships with weather, clocks and a search
function, so it cannot read its own files, run a command or change its code.
The bridge registers this process as an MCP server in upstream's config, which
turns every tool in ``jarvis.tools.TOOLS`` into ``self__<name>`` for the model
(``self__run_command``, ``self__read_file``, and so on).

Upstream's venv runs it because that venv has the ``mcp`` package; the app goes
on PYTHONPATH so we get this repo's ``jarvis`` package, not upstream's:

    JARVIS_APP_DIR=/opt/jarvis PYTHONPATH=/opt/jarvis \
        /opt/jarvis-upstream/.venv/bin/python /opt/jarvis/brain/jarvis_tools_mcp.py

Only stdio is used. Nothing here prints to stdout except the MCP protocol, so
a chatty tool cannot corrupt the stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Desktop-only tools: this box has no session, no browser and no GUI, and the
# model would waste turns on them.
HIDDEN = {"open_target", "launch_app"}


def _app_dir() -> Path:
    configured = os.environ.get("JARVIS_APP_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent


def _import_toolbox():
    app = _app_dir()
    if str(app) not in sys.path:
        sys.path.insert(0, str(app))
    from jarvis.config import memory_file, state_dir
    from jarvis.learning import Learnings
    from jarvis.skills import Skills
    from jarvis.tools import TOOLS, Toolbox
    from jarvis.memory import Memory

    home = state_dir()
    toolbox = Toolbox(
        memory=Memory(memory_file()),
        # There is no phone notification in this path: the upstream brain is
        # the owner's agent, and the owner asked it to run things.
        auto_approve=True,
        skills=Skills(home / "skills"),
        learnings=Learnings(home / "learnings.json"),
    )
    return toolbox, TOOLS


def _specs() -> List[Dict[str, Any]]:
    _, tools = _import_toolbox()
    return [spec for spec in tools if spec["function"]["name"] not in HIDDEN]


def _call(name: str, arguments: Dict[str, Any]) -> str:
    toolbox, tools = _import_toolbox()
    available = {spec["function"]["name"] for spec in tools}
    if name not in available or name in HIDDEN:
        return "ERROR: unknown tool '%s'. Available: %s" % (
            name,
            ", ".join(sorted(available - HIDDEN)),
        )
    if not isinstance(arguments, dict):
        return "ERROR: tool arguments must be a JSON object"
    # Tools talk to the machine, not to the protocol: keep anything they print
    # away from the stdout stream the MCP transport owns.
    with contextlib.redirect_stdout(sys.stderr):
        return str(toolbox.run(name, arguments))


def _tool_defs() -> List[Dict[str, Any]]:
    defs = []
    for spec in _specs():
        fn = spec["function"]
        defs.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return defs


async def _serve() -> None:
    import inspect

    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    async def answer(name: str, arguments: Any) -> str:
        return await asyncio.to_thread(_call, name, arguments or {})

    # mcp 2.x takes handlers as arguments; 1.x used decorators. Upstream's
    # venv decides which one is installed, so support both.
    if "on_list_tools" in inspect.signature(Server.__init__).parameters:

        async def list_tools(_context: Any, _params: Any) -> Any:
            return ListToolsResult(
                tools=[
                    Tool(
                        name=d["name"],
                        description=d["description"],
                        input_schema=d["schema"],
                    )
                    for d in _tool_defs()
                ]
            )

        async def call_tool(_context: Any, params: Any) -> Any:
            text = await answer(params.name, params.arguments)
            return CallToolResult(content=[TextContent(type="text", text=text)])

        server = Server("jarvis", on_list_tools=list_tools, on_call_tool=call_tool)

    else:
        server = Server("jarvis")

        @server.list_tools()
        async def list_tools_v1() -> Any:
            return [
                Tool(name=d["name"], description=d["description"], inputSchema=d["schema"])
                for d in _tool_defs()
            ]

        @server.call_tool()
        async def call_tool_v1(name: str, arguments: Dict[str, Any]) -> Any:
            text = await answer(name, arguments)
            return [TextContent(type="text", text=text)]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> int:
    try:
        _import_toolbox()
    except Exception as exc:  # report before the transport starts
        sys.stderr.write("jarvis tools MCP: cannot load the tool box: %s: %s\n" % (type(exc).__name__, exc))
        return 1
    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
