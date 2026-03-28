from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from nebula_copilot.models import Span, TraceDocument


@dataclass
class SpanDiagnosis:
    span: Span
    error_type: str
    action_suggestion: str


@dataclass
class DiagnosisResult:
    trace_id: str
    bottleneck: SpanDiagnosis
    top_spans: List[SpanDiagnosis]
    total_spans: int

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


def action_suggestion(error_type: str, service_name: str) -> str:
    if error_type == "Timeout":
        return f"优先检查 {service_name} 的下游网络延迟与连接池，日志关键词: timeout/read timed out"
    if error_type == "DB":
        return f"优先检查 {service_name} 的慢 SQL 与锁等待，日志关键词: deadlock/lock wait"
    if error_type == "Downstream":
        return f"优先检查 {service_name} 依赖服务健康状态，日志关键词: 503/connection refused"
    if error_type == "Unknown":
        return f"优先查看 {service_name} ERROR 日志上下文与最近发布记录"
    return f"{service_name} 当前无异常，关注其子调用链路"


def build_span_diagnosis(span: Span) -> SpanDiagnosis:
    err = classify_error(span)
    return SpanDiagnosis(
        span=span,
        error_type=err,
        action_suggestion=action_suggestion(err, span.service_name),
    )


def analyze_trace(trace_doc: TraceDocument, top_n: int = 3) -> DiagnosisResult:
    spans = flatten_spans(trace_doc.root)
    sorted_spans = sorted(spans, key=lambda s: s.duration_ms, reverse=True)
    top = sorted_spans[: max(1, top_n)]
    top_diagnosis = [build_span_diagnosis(s) for s in top]
    return DiagnosisResult(
        trace_id=trace_doc.trace_id,
        bottleneck=top_diagnosis[0],
        top_spans=top_diagnosis,
        total_spans=len(spans),
    )


def build_alert_summary(result: DiagnosisResult) -> str:
    b = result.bottleneck
    stack_preview = (b.span.exception_stack or "无异常栈")[:180]
    return (
        "【Nebula-Copilot 排障摘要】\n"
        f"TraceID: {result.trace_id}\n"
        f"瓶颈服务: {b.span.service_name}\n"
        f"操作: {b.span.operation_name}\n"
        f"耗时: {b.span.duration_ms}ms\n"
        f"异常类型: {b.error_type}\n"
        f"异常摘要: {stack_preview}\n"
        f"建议动作: {b.action_suggestion}"
    )
