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


def _normalize_log_keyword(raw_keyword: str) -> str:
    keyword = (raw_keyword or "").strip().lower()
    if keyword in {"", "none", "unknown", "ok", "null"}:
        return ""
    return keyword


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
    if error_type in {"Timeout", "Downstream", "Unknown", "None"}:
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


def _node_report(state: AgentState, service_name: str, llm_executor: Any | None = None) -> None:
    bottleneck = state.diagnosis.get("bottleneck", {}) if isinstance(state.diagnosis, dict) else {}
    error_type = bottleneck.get("error_type", "Unknown")
    action_hint = str(bottleneck.get("action_suggestion") or "").strip()
    jvm = state.jvm_metrics if isinstance(state.jvm_metrics, dict) else {}
    logs = state.logs if isinstance(state.logs, dict) else {}

    jvm_status = str(jvm.get("status") or "not_queried")
    if jvm_status == "ok":
        jvm_hint = (
            f"JVM证据: p95={jvm.get('p95_duration_ms')}ms, "
            f"heap={jvm.get('heap_used_mb')}/{jvm.get('heap_max_mb')}MB, "
            f"gc_count={jvm.get('gc_count')}, error_rate={jvm.get('error_rate')}"
        )
    elif jvm_status == "no_data":
        jvm_hint = "JVM证据: ES中未检索到对应服务指标"
    elif jvm_status == "not_queried":
        jvm_hint = "JVM证据: 本次路由未查询"
    else:
        jvm_hint = "JVM证据: 暂不可用"

    logs_status = str(logs.get("status") or "not_queried")
    samples = logs.get("sample") if isinstance(logs.get("sample"), list) else []
    sample_preview = str(samples[0]) if samples else "无匹配日志"
    if logs_status == "ok":
        logs_hint = f"日志证据: 命中{logs.get('doc_count')}条, 示例={sample_preview}"
    elif logs_status == "no_data":
        logs_hint = "日志证据: ES中未检索到匹配日志"
    elif logs_status == "not_queried":
        logs_hint = "日志证据: 本次路由未查询"
    else:
        logs_hint = "日志证据: 暂不可用"

    base_summary = (
        f"run_id={state.run_id} trace={state.trace_id} 图执行完成，瓶颈服务={service_name}，"
        f"异常类型={error_type}。{jvm_hint}。{logs_hint}。"
        f"建议动作: {action_hint or '优先检查关键错误日志与依赖可用性。'}"
    )
    state.summary = base_summary
    if llm_executor is not None:
        try:
            polished = llm_executor.polish_summary(base_summary)
            if polished:
                state.summary = polished
                state.add_event("report_polish", "ok", "LLM 报告润色完成", {"enabled": True})
            else:
                state.add_event("report_polish", "skipped", "LLM 未返回可用润色结果", {"enabled": True})
        except Exception as exc:
            state.add_event("report_polish", "fallback", "LLM 润色失败，已降级使用规则摘要", {"error": str(exc)})
    state.add_event("report", "ok", "生成汇总报告", {"summary": state.summary})


def _node_notify(state: AgentState) -> None:
    state.add_event("notify", "ok", "通知阶段完成（由上层 CLI 控制实际发送）", {"summary": state.summary or ""})


def run_agent_graph(
    trace_id: str,
    run_id: str,
    trace_doc: TraceDocument,
    tool_registry: ToolRegistry,
    llm_executor: Any | None = None,
) -> Dict[str, Any]:
    state = AgentState.new(trace_id=trace_id, run_id=run_id)

    try:
        _node_get_trace(state, tool_registry)
        _node_analyze(state, trace_doc)

        bottleneck = state.diagnosis.get("bottleneck", {})
        service_name = str(bottleneck.get("service_name", "unknown-service"))
        keyword = _normalize_log_keyword(str(state.trace_payload.get("keyword") or bottleneck.get("error_type") or ""))

        route = _route_error_type(state)
        state.add_event("route", "ok", "条件路由完成", {"route": route})

        if route == "dual":
            _node_enrich_jvm(state, tool_registry, service_name)
            _node_enrich_logs(state, tool_registry, service_name, keyword)
        elif route == "jvm":
            _node_enrich_jvm(state, tool_registry, service_name)
        else:
            _node_enrich_logs(state, tool_registry, service_name, keyword)

        _node_report(state, service_name, llm_executor=llm_executor)
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
