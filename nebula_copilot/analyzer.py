from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nebula_copilot.knowledge_base import KnowledgeBase, KnowledgeInsight
from nebula_copilot.models import Span, TraceDocument


def _rule_based_action_suggestion(error_type: str, service_name: str) -> str:
    if error_type == "Timeout":
        return f"优先检查 {service_name} 的下游网络延迟与连接池，日志关键词: timeout/read timed out"
    if error_type == "DB":
        return f"优先检查 {service_name} 的慢 SQL 与锁等待，日志关键词: deadlock/lock wait"
    if error_type == "Downstream":
        return f"优先检查 {service_name} 依赖服务健康状态，日志关键词: 503/connection refused"
    if error_type == "Unknown":
        return f"优先查看 {service_name} ERROR 日志上下文与最近发布记录"
    return f"{service_name} 当前无异常，关注其子调用链路"


@dataclass
class SpanDiagnosis:
    span: Span
    error_type: str
    action_suggestion: str
    knowledge_insight: Optional[KnowledgeInsight] = None


@dataclass
class DiagnosisResult:
    trace_id: str
    bottleneck: SpanDiagnosis
    top_spans: List[SpanDiagnosis]
    total_spans: int

    @staticmethod
    def _compact_insight(insight: Optional[KnowledgeInsight]) -> Optional[Dict[str, Any]]:
        if insight is None:
            return None
        compact_patterns: List[Dict[str, Any]] = []
        for item in insight.matched_patterns[:2]:
            compact_patterns.append(
                {
                    "name": item.get("name"),
                    "label": item.get("label"),
                    "confidence": item.get("confidence"),
                    "signals": list(item.get("signals", []))[:3],
                }
            )
        return {
            "matched_patterns": compact_patterns,
            "related_services": insight.related_services,
            "relation_query_hint": insight.relation_query_hint,
            "linkage_investigation_suggestion": insight.linkage_investigation_suggestion,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "total_spans": self.total_spans,
            "bottleneck": {
                "service_name": self.bottleneck.span.service_name,
                "operation_name": self.bottleneck.span.operation_name,
                "duration_ms": self.bottleneck.span.duration_ms,
                "status": self.bottleneck.span.status,
                "error_type": self.bottleneck.error_type,
                "exception_stack": self.bottleneck.span.exception_stack,
                "action_suggestion": self.bottleneck.action_suggestion,
                "knowledge_insight": self._compact_insight(self.bottleneck.knowledge_insight),
            },
            "top_spans": [
                {
                    "service_name": item.span.service_name,
                    "operation_name": item.span.operation_name,
                    "duration_ms": item.span.duration_ms,
                    "status": item.span.status,
                    "error_type": item.error_type,
                    "exception_stack": item.span.exception_stack,
                    "action_suggestion": item.action_suggestion,
                    "knowledge_insight": self._compact_insight(item.knowledge_insight),
                }
                for item in self.top_spans
            ],
            "summary": (
                f"瓶颈节点: {self.bottleneck.span.service_name}, "
                f"耗时: {self.bottleneck.span.duration_ms}ms, "
                f"异常类型: {self.bottleneck.error_type}"
            ),
        }


def flatten_spans(root: Span) -> List[Span]:
    spans: List[Span] = []

    def _dfs(node: Span) -> None:
        spans.append(node)
        for child in node.children:
            _dfs(child)

    _dfs(root)
    return spans


def classify_error(span: Span) -> str:
    stack = (span.exception_stack or "").lower()
    if "timeout" in stack or "timed out" in stack:
        return "Timeout"
    if "deadlock" in stack or "lock wait timeout" in stack or "sql" in stack:
        return "DB"
    if "connection refused" in stack or "503" in stack or "downstream" in stack:
        return "Downstream"
    if span.status.upper() == "ERROR":
        return "Unknown"
    return "None"


def action_suggestion(
    error_type: str,
    service_name: str,
    exception_stack: str | None = None,
    llm_executor: Optional[Any] = None,
) -> str:
    if llm_executor is not None:
        try:
            suggested = llm_executor.suggest_action(error_type, service_name, exception_stack)
            if suggested:
                return suggested
        except Exception:
            # LLM不可用时必须回退到规则逻辑，保证主链路稳定。
            pass
    return _rule_based_action_suggestion(error_type, service_name)


def build_span_diagnosis(
    trace_doc: TraceDocument,
    span: Span,
    llm_executor: Optional[Any] = None,
    knowledge_base: Optional[KnowledgeBase] = None,
) -> SpanDiagnosis:
    err = classify_error(span)
    kb = knowledge_base or KnowledgeBase()
    insight = kb.infer(trace_doc, span, err)
    return SpanDiagnosis(
        span=span,
        error_type=err,
        action_suggestion=action_suggestion(err, span.service_name, span.exception_stack, llm_executor),
        knowledge_insight=insight,
    )


def analyze_trace(trace_doc: TraceDocument, top_n: int = 3, llm_executor: Optional[Any] = None) -> DiagnosisResult:
    spans = flatten_spans(trace_doc.root)
    # ES按span文档拼接trace时会生成合成根节点trace-root，需排除以避免误判瓶颈。
    candidates = [s for s in spans if s.service_name != "trace-root"]
    if not candidates:
        candidates = spans
    sorted_spans = sorted(candidates, key=lambda s: s.duration_ms, reverse=True)
    top = sorted_spans[: max(1, top_n)]
    kb = KnowledgeBase()
    top_diagnosis = [build_span_diagnosis(trace_doc, s, llm_executor, kb) for s in top]
    return DiagnosisResult(
        trace_id=trace_doc.trace_id,
        bottleneck=top_diagnosis[0],
        top_spans=top_diagnosis,
        total_spans=len(spans),
    )


def build_alert_summary(result: DiagnosisResult) -> str:
    b = result.bottleneck
    stack_preview = (b.span.exception_stack or "无异常栈")[:180]
    insight = b.knowledge_insight
    pattern_text = "无"
    relation_hint = "无"
    linkage_hint = "无"
    if insight:
        patterns = insight.matched_patterns
        if patterns:
            pattern_text = ", ".join(str(item.get("label", "")) for item in patterns[:2] if item.get("label")) or "无"
        relation_hint = insight.relation_query_hint or "无"
        linkage_hint = insight.linkage_investigation_suggestion or "无"
    return (
        "【Nebula-Copilot 排障摘要】\n"
        f"TraceID: {result.trace_id}\n"
        f"瓶颈服务: {b.span.service_name}\n"
        f"操作: {b.span.operation_name}\n"
        f"耗时: {b.span.duration_ms}ms\n"
        f"异常类型: {b.error_type}\n"
        f"模式比对: {pattern_text}\n"
        f"关联查询: {relation_hint}\n"
        f"链路排查建议: {linkage_hint}\n"
        f"异常摘要: {stack_preview}\n"
        f"建议动作: {b.action_suggestion}"
    )
