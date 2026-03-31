from __future__ import annotations

from datetime import datetime
from time import sleep
from typing import Any, Dict, Optional

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


def _alert_level(error_type: str, duration_ms: int) -> str:
    et = (error_type or "").strip()
    if et in {"DB", "Downstream", "Timeout"}:
        return "P1"
    if et in {"Unknown"}:
        return "P2"
    if duration_ms >= 1500:
        return "P2"
    return "P3"


def _alert_type_label(error_type: str) -> str:
    mapping = {
        "Timeout": "下游超时",
        "DB": "数据库异常",
        "Downstream": "依赖不可用",
        "Unknown": "未知异常",
        "None": "慢调用",
    }
    return mapping.get(error_type, "链路异常")


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


def _node_report(
    state: AgentState,
    service_name: str,
    llm_executor: Any | None = None,
    *,
    llm_decision_required: bool = False,
    history_store: Any = None,
) -> None:
    bottleneck = state.diagnosis.get("bottleneck", {}) if isinstance(state.diagnosis, dict) else {}
    error_type = bottleneck.get("error_type", "Unknown")
    action_hint = str(bottleneck.get("action_suggestion") or "").strip()
    operation_name = str(bottleneck.get("operation_name") or "unknown-operation")
    duration_ms = int(bottleneck.get("duration_ms") or 0)
    level = _alert_level(str(error_type), duration_ms)
    type_label = _alert_type_label(str(error_type))
    jvm = state.jvm_metrics if isinstance(state.jvm_metrics, dict) else {}
    logs = state.logs if isinstance(state.logs, dict) else {}
    llm_root_cause = ""
    llm_confidence: float | None = None
    llm_linkage_suspected = False
    llm_linkage_suggestion = ""
    knowledge_insight = bottleneck.get("knowledge_insight") if isinstance(bottleneck, dict) else None
    kb_pattern_text = "无"
    kb_relation_hint = "无"
    kb_linkage_hint = ""
    vector_evidence = ""
    historical_cases_context = ""

    if isinstance(knowledge_insight, dict):
        patterns = knowledge_insight.get("matched_patterns")
        if isinstance(patterns, list) and patterns:
            labels = [str(item.get("label", "")).strip() for item in patterns if isinstance(item, dict)]
            labels = [label for label in labels if label]
            if labels:
                kb_pattern_text = "、".join(labels[:2])

            # Collect vector evidence details
            vector_matches = [
                item for item in patterns
                if isinstance(item, dict) and str(item.get("match_source", "")).strip() == "vector"
            ]
            if vector_matches:
                vector_lines = []
                for vm in vector_matches[:2]:
                    score = vm.get("vector_score")
                    label = str(vm.get("label", "")).strip()
                    provider = str(vm.get("vector_provider", "local")).strip()
                    if label and score is not None:
                        vector_lines.append(f"  • {label} (相似度: {score:.4f}, 库: {provider})")
                if vector_lines:
                    vector_evidence = "向量匹配模式:\n" + "\n".join(vector_lines)

        relation_hint = str(knowledge_insight.get("relation_query_hint") or "").strip()
        if relation_hint:
            kb_relation_hint = relation_hint
        kb_linkage_hint = str(knowledge_insight.get("linkage_investigation_suggestion") or "").strip()

    # Retrieve historical cases for context
    if history_store is not None:
        try:
            exception_stack = bottleneck.get("exception_stack")
            historical_matches = history_store.search(
                service_name=service_name,
                operation_name=operation_name,
                error_type=error_type,
                exception_stack=exception_stack,
            )
            if historical_matches:
                case_lines = []
                for i, match in enumerate(historical_matches[:3], 1):
                    case_lines.append(
                        f"{i}. {match.service_name}/{match.error_type} "
                        f"(相似度:{match.score:.2f})\n"
                        f"   建议: {match.action_suggestion[:100]}"
                    )
                if case_lines:
                    historical_cases_context = "历史相似案例:\n" + "\n".join(case_lines)
                    state.add_event(
                        "history_retrieval",
                        "ok",
                        f"检索到 {len(historical_matches)} 个相似历史案例",
                        {
                            "count": len(historical_matches),
                            "top_score": historical_matches[0].score if historical_matches else 0.0,
                        },
                    )
        except Exception as exc:
            state.add_event(
                "history_retrieval",
                "fallback",
                "历史案例检索失败",
                {"error": str(exc)},
            )

    if llm_executor is None and llm_decision_required:
        raise RuntimeError("LLM decision required but executor is not configured")

    if llm_executor is not None:
        try:
            diagnose_fn = getattr(llm_executor, "diagnose_incident", None)
            if callable(diagnose_fn):
                decision = diagnose_fn(
                    {
                        "trace_id": state.trace_id,
                        "service_name": service_name,
                        "error_type": error_type,
                        "operation_name": operation_name,
                        "duration_ms": duration_ms,
                        "jvm": jvm,
                        "logs": logs,
                        "rule_action": action_hint,
                        "historical_cases": historical_cases_context if historical_cases_context else None,
                    }
                )
                if isinstance(decision, dict) and decision:
                    decided_type = str(decision.get("problem_type") or "").strip()
                    if decided_type:
                        error_type = decided_type
                    decided_action = str(decision.get("action") or "").strip()
                    if decided_action:
                        action_hint = decided_action
                    llm_root_cause = str(decision.get("root_cause") or "").strip()
                    conf = decision.get("confidence")
                    if isinstance(conf, (int, float)):
                        llm_confidence = max(0.0, min(1.0, float(conf)))
                    llm_linkage_suspected = bool(decision.get("linkage_suspected") is True)
                    llm_linkage_suggestion = str(decision.get("linkage_action") or "").strip()
                    if not llm_linkage_suspected and not llm_linkage_suggestion:
                        root_lower = llm_root_cause.lower()
                        if any(token in root_lower for token in ("链路", "backpressure", "积压", "依赖", "下游")):
                            llm_linkage_suspected = True
                    state.add_event("llm_decision", "ok", "LLM 根因决策完成", {"decision": decision})
                else:
                    state.add_event("llm_decision", "skipped", "LLM 未返回可用决策", {"enabled": True})
                    if llm_decision_required:
                        raise RuntimeError("LLM decision required but no valid decision returned")
            else:
                state.add_event("llm_decision", "skipped", "LLM 不支持结构化决策接口", {"enabled": True})
                if llm_decision_required:
                    raise RuntimeError("LLM decision required but diagnose_incident is unavailable")
        except Exception as exc:
            state.add_event("llm_decision", "fallback", "LLM 决策失败，已回退规则结论", {"error": str(exc)})
            if llm_decision_required:
                raise RuntimeError(f"LLM decision required but failed: {exc}") from exc

    level = _alert_level(str(error_type), duration_ms)
    type_label = _alert_type_label(str(error_type))

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
        f"【Nebula 告警】[{level}] {type_label}\n"
        "\n"
        "[事件概览]\n"
        f"Trace: {state.trace_id}\n"
        f"Run: {state.run_id}\n"
        f"瓶颈服务: {service_name}\n"
        f"操作: {operation_name}\n"
        f"耗时: {duration_ms}ms\n"
        "\n"
        "[诊断结论]\n"
        f"异常类型: {error_type}\n"
        f"模式比对: {kb_pattern_text}\n"
        f"关联查询: {kb_relation_hint}\n"
        f"LLM根因: {llm_root_cause or '无（规则结论）'}\n"
        f"LLM置信度: {f'{llm_confidence:.2f}' if llm_confidence is not None else 'N/A'}\n"
        "\n"
        "[关键证据]\n"
        f"{jvm_hint}\n"
        f"{logs_hint}\n"
        + (f"{vector_evidence}\n" if vector_evidence else "")
        + (f"{historical_cases_context}\n" if historical_cases_context else "")
        + f"链路排查建议: {llm_linkage_suggestion or kb_linkage_hint or '按调用链顺序补齐证据后再定位首个失败节点。'}\n"
        "\n"
        "[建议动作]\n"
        f"建议动作: {action_hint or '优先检查关键错误日志与依赖可用性。'}"
    )
    mandatory_lines = [
        f"模式比对: {kb_pattern_text}",
        f"关联查询: {kb_relation_hint}",
        f"链路排查建议: {llm_linkage_suggestion or kb_linkage_hint or '按调用链顺序补齐证据后再定位首个失败节点。'}",
    ]

    def _ensure_mandatory_lines(summary_text: str) -> str:
        text = summary_text or ""
        missing = [line for line in mandatory_lines if line not in text]
        if not missing:
            return text
        append_block = "\n" + "\n".join(missing)
        return f"{text}{append_block}" if text else "\n".join(missing)

    state.summary = _ensure_mandatory_lines(base_summary)
    if llm_executor is not None:
        try:
            polished = llm_executor.polish_summary(base_summary)
            if polished:
                state.summary = _ensure_mandatory_lines(polished)
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
    llm_decision_required: bool = False,
    history_store: Any = None,
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

        _node_report(
            state,
            service_name,
            llm_executor=llm_executor,
            llm_decision_required=llm_decision_required,
            history_store=history_store,
        )
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
