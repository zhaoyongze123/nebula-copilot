from __future__ import annotations

from typing import Any, Dict

from nebula_copilot.analyzer import analyze_trace
from nebula_copilot.models import TraceDocument
from nebula_copilot.tools.response import build_tool_response


def analyze_trace_tool(trace: TraceDocument) -> Dict[str, Any]:
    result = analyze_trace(trace)
    payload = result.to_dict()
    summary = f"诊断完成：trace={trace.trace_id}，瓶颈服务={result.bottleneck.span.service_name}"
    return build_tool_response("tool_analyze_trace", trace.trace_id, payload, summary=summary)
