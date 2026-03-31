from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from nebula_copilot.config import AppConfig, VectorConfig
from nebula_copilot.models import Span, TraceDocument
from nebula_copilot.vector_store import VectorRecord, VectorStore, build_vector_store


@dataclass(frozen=True)
class FaultPattern:
    name: str
    label: str
    description: str
    signals: Sequence[str]
    related_metric_checks: Sequence[str]
    linkage_suggestion: str


@dataclass
class KnowledgeInsight:
    matched_patterns: List[Dict[str, object]]
    related_services: List[str]
    relation_query_hint: str
    linkage_investigation_suggestion: Optional[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "matched_patterns": self.matched_patterns,
            "related_services": self.related_services,
            "relation_query_hint": self.relation_query_hint,
            "linkage_investigation_suggestion": self.linkage_investigation_suggestion,
        }


class KnowledgeBase:
    def __init__(
        self,
        *,
        vector_config: Optional[VectorConfig] = None,
        vector_store: Optional[VectorStore] = None,
    ) -> None:
        self._patterns: List[FaultPattern] = [
            FaultPattern(
                name="dependency_outage",
                label="依赖挂掉",
                description="下游服务不可用、连接拒绝或网关返回 5xx 导致主链路失败。",
                signals=("connection refused", "503", "downstream", "unavailable", "timed out"),
                related_metric_checks=(
                    "下游服务 error_rate 与 p95 延迟",
                    "调用方连接池使用率与超时率",
                    "网关 5xx 与重试次数",
                ),
                linkage_suggestion="沿调用链逐跳核对下游实例健康、连接池与重试策略，确认是否出现级联超时。",
            ),
            FaultPattern(
                name="consumer_backlog",
                label="消费积压",
                description="消息消费滞后或背压上升，导致请求超时与重试堆积。",
                signals=("kafka", "lag", "backpressure", "retry", "queue", "timeout"),
                related_metric_checks=(
                    "Kafka consumer lag 与消费速率",
                    "队列堆积深度与重试队列增长",
                    "下游依赖吞吐与超时比例",
                ),
                linkage_suggestion="重点检查消息链路是否存在消费滞后，结合 lag 与重试队列趋势确认背压传播路径。",
            ),
            FaultPattern(
                name="config_drift",
                label="配置漂移",
                description="实例配置或版本不一致，导致业务行为与基线偏离。",
                signals=("config", "configuration", "version", "deserialize", "schema", "property"),
                related_metric_checks=(
                    "实例配置版本与发布时间线",
                    "灰度批次错误率差异",
                    "关键参数变更审计记录",
                ),
                linkage_suggestion="对比异常实例与健康实例的配置快照、发布批次和依赖版本，排查漂移来源。",
            ),
        ]
        self._patterns_by_name = {pattern.name: pattern for pattern in self._patterns}

        self._vector_config = vector_config or VectorConfig()
        self._vector_store: Optional[VectorStore] = None
        self._vector_provider = "none"
        if self._vector_config.enabled:
            if vector_store is not None:
                self._vector_store = vector_store
                self._vector_provider = "custom"
            else:
                build_result = build_vector_store(self._vector_config)
                self._vector_store = build_result.store
                self._vector_provider = build_result.provider
            if self._vector_store is not None:
                self._seed_vector_store()

    @classmethod
    def from_app_config(cls, app_config: AppConfig) -> "KnowledgeBase":
        return cls(vector_config=app_config.vector)

    def infer(self, trace_doc: TraceDocument, span: Span, error_type: str) -> KnowledgeInsight:
        if error_type == "None":
            return KnowledgeInsight(
                matched_patterns=[],
                related_services=[],
                relation_query_hint="当前未识别到异常模式，无需额外关联查询。",
                linkage_investigation_suggestion=None,
            )

        text = self._build_text(span, error_type)
        matched: List[Dict[str, object]] = []
        for pattern in self._patterns:
            hit_signals = [token for token in pattern.signals if token in text]
            if not hit_signals:
                continue

            score = min(0.95, 0.4 + 0.12 * len(hit_signals))
            matched.append(
                {
                    "name": pattern.name,
                    "label": pattern.label,
                    "description": pattern.description,
                    "confidence": round(score, 2),
                    "signals": hit_signals,
                    "match_source": "rule",
                    "related_metric_checks": list(pattern.related_metric_checks),
                }
            )

        matched.extend(self._vector_match(text, existing_names={str(item.get("name", "")) for item in matched}))

        if not matched:
            matched = [
                {
                    "name": "unknown_chain_fault",
                    "label": "未命中典型模式",
                    "description": "异常存在，但暂未匹配到预置架构模式，需要人工补充证据。",
                    "confidence": 0.35,
                    "signals": [error_type.lower()],
                    "related_metric_checks": ["关联服务错误率与慢调用趋势", "最近发布/配置变更记录"],
                }
            ]

        matched.sort(key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        top = matched[0]
        related_services = self._related_services(trace_doc, span)
        related_metrics = top.get("related_metric_checks", [])
        if related_services:
            relation_hint = (
                f"建议补查关联服务指标: {', '.join(related_services)}；"
                f"重点关注: {'; '.join(str(item) for item in related_metrics)}"
            )
        else:
            relation_hint = f"建议补查同服务指标，重点关注: {'; '.join(str(item) for item in related_metrics)}"

        linkage_suggestion = self._linkage_suggestion(str(top.get("name", "")))

        return KnowledgeInsight(
            matched_patterns=matched,
            related_services=related_services,
            relation_query_hint=relation_hint,
            linkage_investigation_suggestion=linkage_suggestion,
        )

    def _build_text(self, span: Span, error_type: str) -> str:
        return " ".join(
            [
                (span.exception_stack or "").lower(),
                (span.operation_name or "").lower(),
                (span.service_name or "").lower(),
                (error_type or "").lower(),
            ]
        )

    def _seed_vector_store(self) -> None:
        if self._vector_store is None:
            return

        records = [
            VectorRecord(
                record_id=pattern.name,
                text=" ".join(
                    [
                        pattern.name,
                        pattern.label,
                        pattern.description,
                        " ".join(pattern.signals),
                        " ".join(pattern.related_metric_checks),
                    ]
                ),
                metadata={
                    "name": pattern.name,
                    "label": pattern.label,
                    "description": pattern.description,
                },
            )
            for pattern in self._patterns
        ]
        self._vector_store.upsert(records)

    def _vector_match(self, text: str, existing_names: set[str]) -> List[Dict[str, object]]:
        if self._vector_store is None:
            return []

        hits = self._vector_store.search(text, top_k=self._vector_config.top_k)
        matched: List[Dict[str, object]] = []
        for hit in hits:
            if hit.score < self._vector_config.min_score:
                continue
            pattern_name = hit.metadata.get("name") or hit.record_id
            if pattern_name in existing_names:
                continue

            pattern = self._patterns_by_name.get(pattern_name)
            related_checks = (
                list(pattern.related_metric_checks)
                if pattern is not None
                else ["关联服务错误率与慢调用趋势", "最近发布/配置变更记录"]
            )
            matched.append(
                {
                    "name": pattern_name,
                    "label": hit.metadata.get("label", "向量相似模式"),
                    "description": hit.metadata.get("description", "根据历史案例相似度召回的模式。"),
                    "confidence": round(min(0.92, max(0.35, hit.score)), 2),
                    "signals": [f"vector_similarity:{hit.score:.2f}"],
                    "match_source": "vector",
                    "vector_provider": self._vector_provider,
                    "vector_score": round(hit.score, 4),
                    "related_metric_checks": related_checks,
                }
            )

        return matched

    def _related_services(self, trace_doc: TraceDocument, target: Span) -> List[str]:
        parent, children = self._find_neighbors(trace_doc.root, target.span_id)
        ordered: List[str] = []
        if parent and parent != target.service_name:
            ordered.append(parent)
        for child in children:
            if child != target.service_name and child not in ordered:
                ordered.append(child)
        return ordered

    def _find_neighbors(self, root: Span, target_span_id: str) -> tuple[Optional[str], List[str]]:
        stack: List[tuple[Span, Optional[Span]]] = [(root, None)]
        while stack:
            node, parent = stack.pop()
            if node.span_id == target_span_id:
                child_services = [child.service_name for child in node.children if child.service_name]
                parent_service = parent.service_name if parent else None
                return parent_service, child_services
            for child in node.children:
                stack.append((child, node))
        return None, []

    def _linkage_suggestion(self, pattern_name: str) -> Optional[str]:
        for pattern in self._patterns:
            if pattern.name == pattern_name:
                return pattern.linkage_suggestion
        if pattern_name == "unknown_chain_fault":
            return "建议按调用链顺序补齐 trace/JVM/日志证据，先定位第一个失败节点再向上下游扩展排查。"
        return None