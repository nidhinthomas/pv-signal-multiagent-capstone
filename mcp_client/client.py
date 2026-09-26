"""Thin async stdio MCP client wrapper for `mcp_server/server.py`.

LangGraph nodes import plain async functions from this module -- they must never import
`mcp_server/server.py` directly (CLAUDE.md coding standards), so this wrapper genuinely
spawns the server as a subprocess and talks to it over the MCP stdio protocol via the
`mcp` SDK's client APIs (`mcp.client.stdio.stdio_client` + `mcp.ClientSession`), parsing
results back into plain Python dicts/lists.

Two layers are exposed:

- `MCPClient`: holds one persistent stdio session across many tool calls (connect once,
  call many times, close once). This is what `st.cache_resource` should hold in the
  Streamlit app (ARCHITECTURE.md §2, §6) so a fresh subprocess isn't spawned per call.
- Module-level plain async functions (`query_ae_reports`, `scan_signals`, ...) matching
  each tool name 1:1, backed by a lazily-created default `MCPClient`. These are the
  simplest import for a LangGraph node: `from mcp_client.client import scan_signals`.
"""

from __future__ import annotations

import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEFAULT_SERVER_SCRIPT = str(Path(__file__).resolve().parent.parent / "mcp_server" / "server.py")


def _unwrap_structured(structured: Any) -> Any:
    """Undo the mcp SDK's auto-wrapping of generic (list/dict) tool return types.

    A tool annotated `-> list[dict]` or `-> dict` has its structured output wrapped as
    `{"result": <value>}` by the SDK (generic types don't publish their own schema).
    None of this server's dict-shaped tool results ever have exactly one key named
    "result", so unwrapping only that exact shape is unambiguous.
    """
    if isinstance(structured, dict) and list(structured.keys()) == ["result"]:
        return structured["result"]
    return structured


def _parse_call_tool_result(result: Any) -> Any:
    if getattr(result, "is_error", False):
        parts = [getattr(block, "text", str(block)) for block in getattr(result, "content", [])]
        raise RuntimeError(f"MCP tool call failed: {'; '.join(parts) or result}")

    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return _unwrap_structured(structured)

    # Fallback: reassemble from unstructured text content blocks (each list item may
    # come back as its own TextContent block -- see mcp's func_metadata._convert_to_content).
    texts = [
        json.loads(block.text)
        for block in getattr(result, "content", [])
        if getattr(block, "type", None) == "text"
    ]
    if len(texts) == 1:
        return texts[0]
    return texts


class MCPClient:
    """A persistent async MCP stdio client session against `mcp_server/server.py`."""

    def __init__(
        self,
        server_script: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._server_script = str(server_script or DEFAULT_SERVER_SCRIPT)
        self._env = env
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def connect(self) -> None:
        if self._session is not None:
            return
        params = StdioServerParameters(
            command=sys.executable,
            args=[self._server_script],
            env=self._env,
        )
        exit_stack = AsyncExitStack()
        try:
            read, write = await exit_stack.enter_async_context(stdio_client(params))
            session = await exit_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await exit_stack.aclose()
            raise
        self._exit_stack = exit_stack
        self._session = session

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None

    async def __aenter__(self) -> "MCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        await self.connect()
        assert self._session is not None
        result = await self._session.call_tool(name, arguments or {})
        return _parse_call_tool_result(result)

    # --- One thin method per MCP tool (ARCHITECTURE.md §6.1 signatures) ---

    async def query_ae_reports(
        self,
        drug_name: str | None = None,
        event_name: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return await self.call_tool(
            "query_ae_reports",
            {"drug_name": drug_name, "event_name": event_name, "limit": limit},
        )

    async def scan_signals(self, prr_threshold: float = 2.0, min_cases: int = 3) -> list[dict]:
        return await self.call_tool(
            "scan_signals", {"prr_threshold": prr_threshold, "min_cases": min_cases}
        )

    async def calculate_prr(self, drug_name: str, event_name: str) -> dict:
        return await self.call_tool(
            "calculate_prr", {"drug_name": drug_name, "event_name": event_name}
        )

    async def get_drug_label(self, drug_name: str) -> dict:
        return await self.call_tool("get_drug_label", {"drug_name": drug_name})

    async def search_literature(self, query: str) -> list[dict]:
        return await self.call_tool("search_literature", {"query": query})

    async def get_signal_history(self, drug_name: str, event_name: str) -> dict:
        return await self.call_tool(
            "get_signal_history", {"drug_name": drug_name, "event_name": event_name}
        )

    async def record_signal_decision(
        self,
        drug_name: str,
        event_name: str,
        run_id: str,
        decision: str,
        reviewer_note: str,
        prr_at_decision: float,
        case_count_at_decision: int,
    ) -> dict:
        return await self.call_tool(
            "record_signal_decision",
            {
                "drug_name": drug_name,
                "event_name": event_name,
                "run_id": run_id,
                "decision": decision,
                "reviewer_note": reviewer_note,
                "prr_at_decision": prr_at_decision,
                "case_count_at_decision": case_count_at_decision,
            },
        )


# --- Module-level plain async functions backed by a lazy default client ---

_default_client: MCPClient | None = None


def get_default_client() -> MCPClient:
    """Return the module's lazily-created default `MCPClient` (one persistent session).

    A LangGraph/Streamlit host that wants explicit lifecycle control (e.g. to hold this
    in `st.cache_resource` and `close()` it on teardown) should construct its own
    `MCPClient` instead of relying on this module-level singleton.
    """
    global _default_client
    if _default_client is None:
        _default_client = MCPClient()
    return _default_client


async def query_ae_reports(
    drug_name: str | None = None, event_name: str | None = None, limit: int = 100
) -> list[dict]:
    return await get_default_client().query_ae_reports(drug_name, event_name, limit)


async def scan_signals(prr_threshold: float = 2.0, min_cases: int = 3) -> list[dict]:
    return await get_default_client().scan_signals(prr_threshold, min_cases)


async def calculate_prr(drug_name: str, event_name: str) -> dict:
    return await get_default_client().calculate_prr(drug_name, event_name)


async def get_drug_label(drug_name: str) -> dict:
    return await get_default_client().get_drug_label(drug_name)


async def search_literature(query: str) -> list[dict]:
    return await get_default_client().search_literature(query)


async def get_signal_history(drug_name: str, event_name: str) -> dict:
    return await get_default_client().get_signal_history(drug_name, event_name)


async def record_signal_decision(
    drug_name: str,
    event_name: str,
    run_id: str,
    decision: str,
    reviewer_note: str,
    prr_at_decision: float,
    case_count_at_decision: int,
) -> dict:
    return await get_default_client().record_signal_decision(
        drug_name,
        event_name,
        run_id,
        decision,
        reviewer_note,
        prr_at_decision,
        case_count_at_decision,
    )
