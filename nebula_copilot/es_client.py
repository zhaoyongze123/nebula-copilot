from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from elasticsearch import Elasticsearch

from nebula_copilot.models import Span, TraceDocument


class ESQueryError(RuntimeError):
    pass


def _to_span(node: Dict[str, Any]) -> Span:
    children = node.get("children", []) or []
    return Span(
        span_id=str(node.get("span_id") or node.get("spanId") or ""),
        parent_span_id=node.get("parent_span_id") or node.get("parentSpanId"),
        service_name=str(node.get("service_name") or node.get("serviceName") or "unknown-service"),
        operation_name=str(node.get("operation_name") or node.get("operationName") or "unknown-operation"),
        duration_ms=int(node.get("duration_ms") or node.get("durationMs") or node.get("duration") or 0),
        status=str(node.get("status") or "OK"),
        exception_stack=node.get("exception_stack") or node.get("exceptionStack"),
        children=[_to_span(child) for child in children],
    )


def _build_tree_from_flat_spans(spans: List[Dict[str, Any]]) -> Span:
    if not spans:
        raise ESQueryError("ES document has empty spans list")

    by_id: Dict[str, Span] = {}
    children_map: Dict[str, List[str]] = {}
    roots: List[Span] = []

    for raw in spans:
        span_id = str(raw.get("span_id") or raw.get("spanId") or "")
        if not span_id:
            raise ESQueryError("ES span missing span_id/spanId")
        by_id[span_id] = _to_span({**raw, "children": []})

    for raw in spans:
        span_id = str(raw.get("span_id") or raw.get("spanId"))
        parent = raw.get("parent_span_id") or raw.get("parentSpanId")
        if parent:
            children_map.setdefault(str(parent), []).append(span_id)
        else:
            roots.append(by_id[span_id])

    for parent_id, child_ids in children_map.items():
        if parent_id not in by_id:
            continue
        by_id[parent_id].children = [by_id[cid] for cid in child_ids if cid in by_id]

    if not roots:
        roots = [max(by_id.values(), key=lambda s: s.duration_ms)]

    return roots[0]


def _parse_ts(ts: Any) -> float:
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0
    return 0.0


def _build_tree_from_span_docs(trace_id: str, docs: List[Dict[str, Any]]) -> TraceDocument:
    if not docs:
        raise ESQueryError("No span documents found for trace")

    children = [
        Span(
            span_id=str(d.get("span_id") or d.get("spanId") or f"auto-{i}"),
            parent_span_id="root",
            service_name=str(d.get("service_name") or d.get("serviceName") or "unknown-service"),
            operation_name=str(d.get("operation_name") or d.get("operationName") or d.get("methodName") or "unknown-op"),
            duration_ms=int(d.get("duration_ms") or d.get("durationMs") or d.get("duration") or 0),
            status=str(d.get("status") or "OK"),
            exception_stack=d.get("exception_stack") or d.get("exceptionStack") or d.get("ai_diagnosis"),
            children=[],
        )
        for i, d in enumerate(sorted(docs, key=lambda x: _parse_ts(x.get("timestamp") or x.get("@timestamp"))))
    ]

    root_duration = max((c.duration_ms for c in children), default=0)
    root = Span(
        span_id="root",
        parent_span_id=None,
        service_name="trace-root",
        operation_name=f"trace:{trace_id}",
        duration_ms=root_duration,
        status="ERROR" if any(c.status.upper() == "ERROR" for c in children) else "OK",
        exception_stack=None,
        children=children,
    )
    return TraceDocument(trace_id=trace_id, root=root)


def trace_from_es_source(source: Dict[str, Any]) -> TraceDocument:
    trace_id = str(source.get("trace_id") or source.get("traceId") or "")
    if not trace_id:
        raise ESQueryError("ES document missing trace_id/traceId")

    if isinstance(source.get("root"), dict):
        root = _to_span(source["root"])
        return TraceDocument(trace_id=trace_id, root=root)

    spans = source.get("spans")
    if isinstance(spans, list):
        root = _build_tree_from_flat_spans(spans)
        return TraceDocument(trace_id=trace_id, root=root)

    raise ESQueryError("Unsupported ES trace schema: expected root or spans")


def _build_es(
    es_url: str,
    username: Optional[str],
    password: Optional[str],
    verify_certs: bool,
    timeout_seconds: int,
) -> Elasticsearch:
    auth = (username, password) if username and password else None
    return Elasticsearch(
        hosts=[es_url],
        basic_auth=auth,
        verify_certs=verify_certs,
        request_timeout=timeout_seconds,
    )


def fetch_trace_by_id(
    es_url: str,
    index: str,
    trace_id: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_certs: bool = True,
    timeout_seconds: int = 10,
) -> TraceDocument:
    es = _build_es(es_url, username, password, verify_certs, timeout_seconds)

    query = {
        "size": 500,
        "query": {
            "bool": {
                "should": [
                    {"term": {"trace_id.keyword": trace_id}},
                    {"term": {"traceId.keyword": trace_id}},
                    {"term": {"trace_id": trace_id}},
                    {"term": {"traceId": trace_id}},
                ],
                "minimum_should_match": 1,
            }
        },
        "sort": [
            {"timestamp": {"order": "asc", "unmapped_type": "date"}},
            {"@timestamp": {"order": "asc", "unmapped_type": "date"}},
        ],
    }

    resp = es.search(index=index, body=query)
    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        raise ESQueryError(f"Trace not found in ES. trace_id={trace_id}, index={index}")

    sources = [h.get("_source", {}) for h in hits]

    for src in sources:
        if isinstance(src.get("root"), dict) or isinstance(src.get("spans"), list):
            return trace_from_es_source(src)

    return _build_tree_from_span_docs(trace_id, sources)


def list_recent_trace_ids(
    es_url: str,
    index: str,
    last_minutes: int = 30,
    limit: int = 20,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_certs: bool = True,
    timeout_seconds: int = 10,
) -> List[str]:
    es = _build_es(es_url, username, password, verify_certs, timeout_seconds)
    cutoff_ms = int(datetime.now().timestamp() * 1000) - last_minutes * 60 * 1000

    query = {
        "size": 0,
        "query": {
            "bool": {
                "should": [
                    {"range": {"timestamp": {"gte": cutoff_ms}}},
                    {"range": {"@timestamp": {"gte": f"now-{last_minutes}m", "lte": "now"}}},
                ],
                "minimum_should_match": 1,
            }
        },
        "aggs": {
            "trace_id_snake": {
                "terms": {"field": "trace_id", "size": limit},
                "aggs": {
                    "latest_num": {"max": {"field": "timestamp"}},
                    "latest_date": {"max": {"field": "@timestamp"}},
                },
            },
            "trace_id_camel": {
                "terms": {"field": "traceId", "size": limit},
                "aggs": {
                    "latest_num": {"max": {"field": "timestamp"}},
                    "latest_date": {"max": {"field": "@timestamp"}},
                },
            },
        },
    }

    resp = es.search(index=index, body=query)

    merged: Dict[str, float] = {}
    for agg_name in ("trace_id_snake", "trace_id_camel"):
        buckets = resp.get("aggregations", {}).get(agg_name, {}).get("buckets", [])
        for b in buckets:
            key = b.get("key")
            if not key:
                continue
            latest_num = b.get("latest_num", {}).get("value") or 0
            latest_date = b.get("latest_date", {}).get("value") or 0
            latest = max(float(latest_num), float(latest_date))
            merged[key] = max(merged.get(key, 0.0), latest)

    sorted_ids = sorted(merged.items(), key=lambda x: x[1], reverse=True)
    return [trace_id for trace_id, _ in sorted_ids[:limit]]
