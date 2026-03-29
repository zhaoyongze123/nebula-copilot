from __future__ import annotations

from typing import Any, Dict

from nebula_copilot.tools.response import build_tool_response
from nebula_copilot.tools.types import JVMTool


def get_jvm_metrics_tool(service_name: str, tool: JVMTool) -> Dict[str, Any]:
    payload = tool(service_name)
    summary = f"JVM 指标已获取：service={service_name}"
    return build_tool_response("tool_get_jvm_metrics", service_name, payload, summary=summary)
