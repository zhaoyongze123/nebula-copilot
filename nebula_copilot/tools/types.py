from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol


class TraceTool(Protocol):
    def __call__(self, trace_id: str) -> Dict[str, Any]:
        ...


class JVMTool(Protocol):
    def __call__(self, service_name: str) -> Dict[str, Any]:
        ...


class LogsTool(Protocol):
    def __call__(self, service_name: str, keyword: str) -> Dict[str, Any]:
        ...


@dataclass
class ToolRegistry:
    query_trace: TraceTool
    query_jvm: JVMTool
    query_logs: LogsTool


@dataclass
class AgentContext:
    trace_id: str
    tool_registry: ToolRegistry
