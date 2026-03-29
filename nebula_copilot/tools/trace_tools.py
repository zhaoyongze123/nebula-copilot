from __future__ import annotations

from typing import Any, Dict

from nebula_copilot.repository import LocalJsonRepository
from nebula_copilot.tools.response import build_tool_response
from nebula_copilot.tools.types import TraceTool


def get_trace_payload(trace_id: str, source_path: str) -> Dict[str, Any]:
    repository = LocalJsonRepository(source_path)
    trace_doc = repository.get_trace(trace_id)
    return {
        "trace_id": trace_doc.trace_id,
        "bottleneck_service": trace_doc.root.service_name,
        "keyword": trace_doc.root.status.lower(),
    }


def get_trace_tool(trace_id: str, tool: TraceTool) -> Dict[str, Any]:
    payload = tool(trace_id)
    summary = f"已获取 Trace：{trace_id}"
    return build_tool_response("tool_get_trace", trace_id, payload, summary=summary)
