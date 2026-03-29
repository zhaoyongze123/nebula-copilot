from __future__ import annotations

from datetime import datetime
from time import sleep
from typing import Any, Dict

from nebula_copilot.agent.state import AgentState
from nebula_copilot.models import TraceDocument
from nebula_copilot.tooling import tool_analyze_trace, tool_get_jvm_metrics, tool_get_trace, tool_search_logs
from nebula_copilot.tools.types import ToolRegistry


_MAX_RETRY = 2
_RETRY_BACKOFF_SECONDS = 0.05


def _run_with_retry(state: AgentState, node: str, fn: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    attempt = 0
    while True:
        attempt += 1
        try:
            result = fn(*args, **kwargs)
            if attempt > 1:
                state.add_event(
                    node,
                    "retry_ok",
                    "重试成功",
                    {"attempt": attempt},
                )
            return result
        except Exception as exc:  # pragma: no cover
            state.add_event(
                node,
                "retry_failed",
                "节点执行失败，准备重试",
                {"attempt": attempt, "error": str(exc)},
            )
            if attempt > _MAX_RETRY:
                raise
            sleep(_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))


def _node_get_trace(state: AgentState, tool_registry: ToolRegistry) -> None:
    result = _run_with_retry(state, "get_trace", tool_get_trace, state.trace_id, tool_registry.query_trace)
    state.trace_payload = result.get("payload", {})
    state.add_event("get_trace", "ok", "trace 拉取完成", result)


def _node_analyze(state: AgentState, trace_doc: TraceDocument) -> None:
    result = tool_analyze_trace(trace_doc)
    state.diagnosis = result.get("payload", {})
    state.add_event("analyze", "ok", "诊断完成", result)


def _route_error_type(state: AgentState) -> str:
    bottleneck = state.diagnosis.get("bottleneck", {})
    error_type = str(bottleneck.get("error_type", "Unknown"))
    if error_type in {"Timeout", "Downstream"}:
        return "dual"
    if error_type == "DB":
        return "jvm"
    return "logs"


def _node_enrich_jvm(state: AgentState, tool_registry: ToolRegistry, service_name: str) -> None:
    result = _run_with_retry(state, "enrich_jvm", tool_get_jvm_metrics, service_name, tool_registry.query_jvm)
    state.jvm_metrics = result.get("payload", {})
    state.add_event("enrich_jvm", "ok", "JVM 指标补充完成", result)


def _node_enrich_logs(state: AgentState, tool_registry: ToolRegistry, service_name: str, keyword: str) -> None:
    result = _run_with_retry(state, "enrich_logs", tool_search_logs, service_name, keyword, tool_registry.query_logs)
    state.logs = result.get("payload", {})
    state.add_event("enrich_logs", "ok", "日志补充完成", result)


def _node_report(state: AgentState, service_name: str) -> None:
    error_type = state.diagnosis.get("bottleneck", {}).get("error_type", "Unknown")
    state.summary = (
        f"run_id={state.run_id} trace={state.trace_id} 图执行完成，瓶颈服务={service_name}，"
        f"异常类型={error_type}。建议优先检查连接池、依赖可用性与关键错误日志。"
    )
    state.add_event("report", "ok", "生成汇总报告", {"summary": state.summary})


def _node_notify(state: AgentState) -> None:
    state.add_event("notify", "ok", "通知阶段完成（由上层 CLI 控制实际发送）", {"summary": state.summary or ""})


def run_agent_graph(trace_id: str, run_id: str, trace_doc: TraceDocument, tool_registry: ToolRegistry) -> Dict[str, Any]:
    state = AgentState.new(trace_id=trace_id, run_id=run_id)

    try:
        _node_get_trace(state, tool_registry)
        _node_analyze(state, trace_doc)

        bottleneck = state.diagnosis.get("bottleneck", {})
        service_name = str(bottleneck.get("service_name", "unknown-service"))
        keyword = str(state.trace_payload.get("keyword") or bottleneck.get("error_type") or "timeout").lower()

        route = _route_error_type(state)
        state.add_event("route", "ok", "条件路由完成", {"route": route})

        if route == "dual":
            _node_enrich_jvm(state, tool_registry, service_name)
            _node_enrich_logs(state, tool_registry, service_name, keyword)
        elif route == "jvm":
            _node_enrich_jvm(state, tool_registry, service_name)
        else:
            _node_enrich_logs(state, tool_registry, service_name, keyword)

        _node_report(state, service_name)
        _node_notify(state)
        state.status = "ok"

    except Exception as exc:
        state.status = "failed"
        state.error = str(exc)
        state.add_event("fallback", "failed", "节点执行失败，触发降级", {"error": str(exc)})
        state.summary = (
            f"run_id={state.run_id} trace={state.trace_id} 图执行失败，"
            f"已降级返回。错误={state.error}"
        )

    return {
        "run_id": state.run_id,
        "trace_id": state.trace_id,
        "status": state.status,
        "started_at": state.started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "trace": state.trace_payload,
        "diagnosis": state.diagnosis,
        "jvm": state.jvm_metrics,
        "logs": state.logs,
        "summary": state.summary,
        "error": state.error,
        "history": state.history,
    }
