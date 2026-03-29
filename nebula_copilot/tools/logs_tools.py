from __future__ import annotations

from typing import Any, Dict

from nebula_copilot.tools.response import build_tool_response
from nebula_copilot.tools.types import LogsTool


def search_logs_tool(service_name: str, keyword: str, tool: LogsTool) -> Dict[str, Any]:
    payload = tool(service_name, keyword)
    summary = f"日志检索完成：service={service_name}，keyword={keyword}"
    return build_tool_response("tool_search_logs", service_name, payload, summary=summary)
