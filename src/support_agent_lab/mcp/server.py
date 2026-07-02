from __future__ import annotations

from support_agent_lab.bootstrap import create_container
from support_agent_lab.config import get_settings
from support_agent_lab.mcp.adapter import MCPToolAdapter


def build_adapter() -> MCPToolAdapter:
    settings = get_settings()
    if settings.is_production:
        raise RuntimeError(
            "The bundled MCP server is local-only until MCP auth/session actor propagation is configured."
        )
    container = create_container()
    return MCPToolAdapter(
        container.tools,
        tenant_id=container.orchestrator.tenant_id,
        allow_default_actor=True,
        auto_idempotency_key=True,
    )


def main() -> None:
    """Run a real MCP server when the optional MCP SDK is installed.

    This file intentionally keeps the fallback explicit. The README explains how
    the same ToolBroker contracts map to FastMCP tools in production.
    """

    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise SystemExit(
            "Install optional MCP dependencies first: pip install -e '.[mcp]'. "
            f"Original import error: {exc}"
        ) from exc

    adapter = build_adapter()
    server = FastMCP("production-support-agent-lab")

    for tool in adapter.list_tools():
        name = tool["name"]

        async def _call(arguments: dict, tool_name: str = name):
            return await adapter.call_tool(tool_name, arguments)

        server.tool(name=name, description=tool["description"])(_call)

    server.run()


if __name__ == "__main__":
    main()
