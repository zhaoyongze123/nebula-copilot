from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from elasticsearch import BadRequestError, Elasticsearch

from nebula_copilot.models import Span, TraceDocument


class ESQueryError(RuntimeError):
    pass


def _extract_by_path(payload: Dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _first_present(payload: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if "." in key:
            value = _extract_by_path(payload, key)
        else:
            value = payload.get(key)
        if value is not None:
            return value
    return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hits_total_value(hits_block: Dict[str, Any]) -> int:
    total = hits_block.get("total")
    if isinstance(total, dict):
        return int(total.get("value") or 0)
    if isinstance(total, (int, float)):
        return int(total)
    return 0


def _service_filter(service_name: str) -> Dict[str, Any]:
    return {
        "bool": {
            "should": [
                {"term": {"service_name.keyword": service_name}},
                {"term": {"serviceName.keyword": service_name}},
                {"term": {"service_name": service_name}},
                {"term": {"serviceName": service_name}},
            ],
            "minimum_should_match": 1,
        }
    }


def _time_filter(last_minutes: int) -> Dict[str, Any]:
    cutoff_ms = int(datetime.now().timestamp() * 1000) - last_minutes * 60 * 1000
    return {
        "bool": {
            "should": [
                {"range": {"timestamp": {"gte": cutoff_ms}}},
                {"range": {"@timestamp": {"gte": f"now-{last_minutes}m", "lte": "now"}}},
            ],
            "minimum_should_match": 1,
        }
    }


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

    # Prefer real parent-child reconstruction when span docs contain parent ids.
    # This preserves true topology edges instead of flattening all spans under a synthetic root.
    ordered_docs = sorted(docs, key=lambda x: _parse_ts(x.get("timestamp") or x.get("@timestamp")))
    try:
        root = _build_tree_from_flat_spans(ordered_docs)
        return TraceDocument(trace_id=trace_id, root=root)
    except ESQueryError:
        # Fallback for incomplete docs that miss span_id/parent fields.
        pass

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

    def _build_query(field: str) -> Dict[str, Any]:
        return {
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
                "trace_ids": {
                    "terms": {"field": field, "size": limit},
                    "aggs": {
                        "latest_num": {"max": {"field": "timestamp"}},
                        "latest_date": {"max": {"field": "@timestamp"}},
                    },
                }
            },
        }

    candidate_fields = [
        "trace_id.keyword",
        "traceId.keyword",
        "trace_id",
        "traceId",
    ]

    merged: Dict[str, float] = {}
    for field in candidate_fields:
        try:
            resp = es.search(index=index, body=_build_query(field))
        except BadRequestError as exc:
            if "Fielddata is disabled" in str(exc):
                continue
            raise

        buckets = resp.get("aggregations", {}).get("trace_ids", {}).get("buckets", [])
        for b in buckets:
            key = b.get("key")
            if not key:
                continue
            latest_num = b.get("latest_num", {}).get("value") or 0
            latest_date = b.get("latest_date", {}).get("value") or 0
            latest = max(float(latest_num), float(latest_date))
            merged[str(key)] = max(merged.get(str(key), 0.0), latest)

    sorted_ids = sorted(merged.items(), key=lambda x: x[1], reverse=True)
    return [trace_id for trace_id, _ in sorted_ids[:limit]]


def query_service_jvm_metrics(
    es_url: str,
    index: str,
    service_name: str,
    last_minutes: int = 30,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_certs: bool = True,
    timeout_seconds: int = 10,
) -> Dict[str, Any]:
    try:
        es = _build_es(es_url, username, password, verify_certs, timeout_seconds)
        query = {
            "size": 1,
            "query": {
                "bool": {
                    "filter": [
                        _service_filter(service_name),
                        _time_filter(last_minutes),
                    ]
                }
            },
            "sort": [
                {"timestamp": {"order": "desc", "unmapped_type": "date"}},
                {"@timestamp": {"order": "desc", "unmapped_type": "date"}},
            ],
            "aggs": {
                "error_docs": {
                    "filter": {
                        "bool": {
                            "should": [
                                {"term": {"status.keyword": "ERROR"}},
                                {"term": {"status": "ERROR"}},
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                },
                "p95_duration_ms": {"percentiles": {"field": "duration_ms", "percents": [95]}},
                "p95_duration": {"percentiles": {"field": "duration", "percents": [95]}},
            },
        }
        resp = es.search(index=index, body=query)
        hits = resp.get("hits", {}).get("hits", [])
        total = _hits_total_value(resp.get("hits", {}))
        latest = hits[0].get("_source", {}) if hits else {}

        heap_used = _safe_float(
            _first_present(
                latest,
                [
                    "jvm.heap.used",
                    "jvm.heap_used",
                    "jvmHeapUsed",
                    "jvm_heap_used_mb",
                    "heap_used_mb",
                    "heapUsedMb",
                ],
            )
        )
        heap_max = _safe_float(
            _first_present(
                latest,
                [
                    "jvm.heap.max",
                    "jvm.heap_max",
                    "jvmHeapMax",
                    "jvm_heap_max_mb",
                    "heap_max_mb",
                    "heapMaxMb",
                ],
            )
        )
        gc_count = _safe_int(
            _first_present(
                latest,
                [
                    "jvm.gc.count",
                    "jvm.gc_count",
                    "jvmGcCount",
                    "gc_count",
                    "gcCount",
                ],
            )
        )
        thread_count = _safe_int(
            _first_present(
                latest,
                [
                    "jvm.threads.live",
                    "jvm.thread_count",
                    "thread_count",
                    "threadCount",
                ],
            )
        )

        p95_ms = resp.get("aggregations", {}).get("p95_duration_ms", {}).get("values", {}).get("95.0")
        if p95_ms is None:
            p95_ms = resp.get("aggregations", {}).get("p95_duration", {}).get("values", {}).get("95.0")
        p95_duration_ms = _safe_float(p95_ms)

        error_count = int(resp.get("aggregations", {}).get("error_docs", {}).get("doc_count") or 0)
        error_rate = (error_count / total) if total > 0 else 0.0

        return {
            "service": service_name,
            "source": "es",
            "index": index,
            "window_minutes": last_minutes,
            "status": "ok" if total > 0 else "no_data",
            "doc_count": total,
            "sample_ts": _first_present(latest, ["@timestamp", "timestamp"]),
            "heap_used_mb": heap_used,
            "heap_max_mb": heap_max,
            "gc_count": gc_count,
            "thread_count": thread_count,
            "p95_duration_ms": p95_duration_ms,
            "error_count": error_count,
            "error_rate": round(error_rate, 4),
        }
    except Exception as exc:  # pragma: no cover
        return {
            "service": service_name,
            "source": "es",
            "index": index,
            "window_minutes": last_minutes,
            "status": "unavailable",
            "error": str(exc),
            "doc_count": 0,
            "heap_used_mb": None,
            "heap_max_mb": None,
            "gc_count": None,
            "thread_count": None,
            "p95_duration_ms": None,
            "error_count": 0,
            "error_rate": 0.0,
        }


def search_service_logs(
    es_url: str,
    index: str,
    service_name: str,
    keyword: str,
    last_minutes: int = 30,
    limit: int = 5,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_certs: bool = True,
    timeout_seconds: int = 10,
) -> Dict[str, Any]:
    try:
        es = _build_es(es_url, username, password, verify_certs, timeout_seconds)
        must_clauses: List[Dict[str, Any]] = []
        if keyword:
            must_clauses.append(
                {
                    "bool": {
                        "should": [
                            {"match_phrase": {"exceptionStack": keyword}},
                            {"match_phrase": {"exception_stack": keyword}},
                            {"match_phrase": {"message": keyword}},
                            {"match_phrase": {"log": keyword}},
                            {"match_phrase": {"ai_diagnosis": keyword}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        query: Dict[str, Any] = {
            "size": max(1, limit),
            "query": {
                "bool": {
                    "filter": [
                        _service_filter(service_name),
                        _time_filter(last_minutes),
                    ],
                    "must": must_clauses,
                }
            },
            "sort": [
                {"timestamp": {"order": "desc", "unmapped_type": "date"}},
                {"@timestamp": {"order": "desc", "unmapped_type": "date"}},
            ],
        }
        resp = es.search(index=index, body=query)
        hits = resp.get("hits", {}).get("hits", [])
        total = _hits_total_value(resp.get("hits", {}))

        samples: List[str] = []
        trace_ids: List[str] = []
        for hit in hits:
            src = hit.get("_source", {})
            message = _first_present(src, ["exception_stack", "exceptionStack", "message", "log", "ai_diagnosis"])
            if isinstance(message, str) and message.strip():
                samples.append(message.strip().replace("\n", " | ")[:220])
            trace_id = _first_present(src, ["trace_id", "traceId"])
            if isinstance(trace_id, str) and trace_id:
                trace_ids.append(trace_id)

        return {
            "service": service_name,
            "keyword": keyword,
            "source": "es",
            "index": index,
            "window_minutes": last_minutes,
            "status": "ok" if total > 0 else "no_data",
            "doc_count": total,
            "sample": samples,
            "trace_ids": trace_ids[: max(1, limit)],
        }
    except Exception as exc:  # pragma: no cover
        return {
            "service": service_name,
            "keyword": keyword,
            "source": "es",
            "index": index,
            "window_minutes": last_minutes,
            "status": "unavailable",
            "error": str(exc),
            "doc_count": 0,
            "sample": [],
            "trace_ids": [],
        }


def search_traces_by_range(
    es_url: str,
    index: str,
    from_date: datetime,
    to_date: datetime,
    limit: int = 1000,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_certs: bool = True,
    timeout_seconds: int = 10,
) -> List[TraceDocument]:
    """按时间范围查询 ES 中的所有 traces.

    Args:
        es_url: Elasticsearch 地址
        index: 索引名称或模式（支持通配符）
        from_date: 开始时间
        to_date: 结束时间
        limit: 最大返回数量
        username: ES 用户名
        password: ES 密码
        verify_certs: 是否验证 SSL 证书
        timeout_seconds: 查询超时（秒）

    Returns:
        TraceDocument 列表

    Raises:
        ESQueryError: 查询失败
    """
    es = _build_es(es_url, username, password, verify_certs, timeout_seconds)

    # 构建时间范围过滤器（转换为 epoch_millis）
    from_ts = int(from_date.timestamp() * 1000)
    to_ts = int(to_date.timestamp() * 1000)

    query = {
        "size": limit,
        "query": {
            "bool": {
                "should": [
                    {
                        "range": {
                            "timestamp": {
                                "gte": from_ts,
                                "lte": to_ts,
                            }
                        }
                    },
                    {
                        "range": {
                            "@timestamp": {
                                "gte": from_ts,
                                "lte": to_ts,
                            }
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        },
        "sort": [
            {"timestamp": {"order": "desc", "unmapped_type": "date"}},
            {"@timestamp": {"order": "desc", "unmapped_type": "date"}},
        ],
    }

    try:
        resp = es.search(index=index, body=query)
    except Exception as exc:
        raise ESQueryError(f"Failed to search traces in ES: {exc}") from exc

    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        return []

    # 按 trace_id 分组，然后为每个 trace 构建 TraceDocument
    traces_by_id: Dict[str, List[Dict[str, Any]]] = {}
    for hit in hits:
        source = hit.get("_source", {})
        trace_id = str(source.get("trace_id") or source.get("traceId") or "")
        if not trace_id:
            continue
        traces_by_id.setdefault(trace_id, []).append(source)

    traces: List[TraceDocument] = []
    for trace_id, sources in traces_by_id.items():
        try:
            # 尝试从单个 source 构建 trace
            for src in sources:
                if isinstance(src.get("root"), dict) or isinstance(src.get("spans"), list):
                    trace = trace_from_es_source(src)
                    traces.append(trace)
                    break
            else:
                # 如果没有找到合适的 source，从多个 span docs 构建
                trace = _build_tree_from_span_docs(trace_id, sources)
                traces.append(trace)
        except Exception as exc:
            # 跳过解析失败的 traces
            continue

    return traces


def query_overview_metrics(
    es_url: str,
    index: str,
    last_hours: int = 24,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_certs: bool = True,
    timeout_seconds: int = 10,
) -> Dict[str, Any]:
    """从 ES 查询概览指标（用于 Dashboard KPI 和图表）。

    Args:
        es_url: Elasticsearch 地址
        index: 索引名称
        last_hours: 回溯时间窗口（小时）
        username: ES 用户名
        password: ES 密码
        verify_certs: 是否验证 SSL 证书
        timeout_seconds: 查询超时（秒）

    Returns:
        包含 total、success_rate、failed、degraded、p95_duration_ms、
        apdex_series、response_time_series 的字典
    """
    es = _build_es(es_url, username, password, verify_certs, timeout_seconds)
    now_ms = int(datetime.now().timestamp() * 1000)
    from_ms = now_ms - last_hours * 3600 * 1000

    # 时间分段桶（用于图表）：每 5 分钟一个桶
    interval_ms = 5 * 60 * 1000
    num_buckets = min(last_hours * 12, 288)  # 最多 288 个桶（24 小时）

    query = {
        "size": 0,
        "query": {
            "bool": {
                "should": [
                    {"range": {"timestamp": {"gte": from_ms, "lte": now_ms}}},
                    {"range": {"@timestamp": {"gte": f"now-{last_hours}h", "lte": "now"}}},
                ],
                "minimum_should_match": 1,
            }
        },
        "aggs": {
            "traces_over_time": {
                "date_histogram": {
                    "field": "timestamp",
                    "fixed_interval": f"{interval_ms}ms",
                    "min_doc_count": 0,
                    "extended_bounds": {"min": from_ms, "max": now_ms},
                },
                "aggs": {
                    "by_trace": {
                        "terms": {
                            "field": "trace_id",
                            "size": 10000,
                        },
                        "aggs": {
                            "has_error": {
                                "filter": {
                                    "bool": {
                                        "should": [
                                            {"term": {"status": "ERROR"}},
                                            {"term": {"status.keyword": "ERROR"}},
                                        ],
                                        "minimum_should_match": 1,
                                    }
                                }
                            },
                            "max_duration": {"max": {"field": "duration_ms"}},
                        },
                    },
                    "total_errors": {
                        "filter": {
                            "bool": {
                                "should": [
                                    {"term": {"status": "ERROR"}},
                                    {"term": {"status.keyword": "ERROR"}},
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    },
                },
            },
            "total_traces": {
                "cardinality": {"field": "trace_id"}
            },
            "error_traces": {
                "filter": {
                    "bool": {
                        "should": [
                            {"term": {"status": "ERROR"}},
                            {"term": {"status.keyword": "ERROR"}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
                "aggs": {
                    "unique_errors": {"cardinality": {"field": "trace_id"}}
                }
            },
            "p95_duration": {"percentiles": {"field": "duration_ms", "percents": [95]}},
            "p99_duration": {"percentiles": {"field": "duration_ms", "percents": [99]}},
            "avg_duration": {"avg": {"field": "duration_ms"}},
        },
    }

    try:
        resp = es.search(index=index, body=query)
    except Exception as exc:
        raise ESQueryError(f"Failed to query overview metrics: {exc}") from exc

    aggs = resp.get("aggregations", {})

    # 解析总 trace 数
    total_traces = int(aggs.get("total_traces", {}).get("value") or 0)
    error_traces = int(aggs.get("error_traces", {}).get("unique_errors", {}).get("value") or 0)

    # 成功数（unique traces - error traces），避免分桶重复计算
    ok_traces = max(0, total_traces - error_traces)
    success_rate = round((ok_traces / total_traces) * 100, 2) if total_traces > 0 else 0.0

    # 降级数：估算（LLM 降级等无法从 span 直接识别，此处用总错误数的 20% 估算）
    degraded = int(error_traces * 0.2)

    # P95/P99/Avg 耗时
    p95_vals = aggs.get("p95_duration", {}).get("values", {})
    p95_duration_ms = int(float(p95_vals.get("95.0") or 0))
    p99_duration_ms = int(float(p95_vals.get("99.0") or 0))
    avg_duration_ms = int(float(aggs.get("avg_duration", {}).get("value") or 0))

    # 时间序列：每个时间桶的 apdex 和响应时间
    buckets = aggs.get("traces_over_time", {}).get("buckets", [])
    apdex_series: List[Dict[str, Any]] = []
    response_time_series: List[Dict[str, Any]] = []

    for bucket in buckets:
        ts_ms = bucket.get("key", 0)
        if not ts_ms:
            continue

        # 该桶内的 trace 统计
        trace_buckets = bucket.get("by_trace", {}).get("buckets", [])
        total_in_bucket = len(trace_buckets)
        if total_in_bucket == 0:
            continue

        error_count = sum(1 for tb in trace_buckets if tb.get("has_error", {}).get("doc_count", 0) > 0)
        ok_count = total_in_bucket - error_count

        # Apdex = (ok + 0.5 * degraded) / total，此处简化为 ok / total
        apdex = ok_count / total_in_bucket if total_in_bucket > 0 else 0

        # 该桶的最大耗时作为响应时间代表
        max_durations = [tb.get("max_duration", {}).get("value", 0) or 0 for tb in trace_buckets]
        max_duration = max(max_durations) if max_durations else 0

        ts_str = datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M")
        apdex_series.append({"time": ts_str, "value": round(apdex, 3)})
        response_time_series.append({"time": ts_str, "value": int(max_duration)})

    return {
        "total": total_traces,
        "success_rate": success_rate,
        "failed": error_traces,
        "degraded": degraded,
        "p95_duration_ms": p95_duration_ms,
        "p99_duration_ms": p99_duration_ms,
        "avg_duration_ms": avg_duration_ms,
        "apdex_series": apdex_series,
        "response_time_series": response_time_series,
        "source": "es",
        "window_hours": last_hours,
    }


def query_recent_traces(
    es_url: str,
    index: str,
    last_minutes: int = 60,
    limit: int = 20,
    username: Optional[str] = None,
    password: Optional[str] = None,
    verify_certs: bool = True,
    timeout_seconds: int = 10,
) -> List[Dict[str, Any]]:
    """从 ES 查询最近的 traces（用于运行记录列表）。

    Returns:
        [{run_id, trace_id, status, started_at, duration_ms, service_name}, ...]
    """
    es = _build_es(es_url, username, password, verify_certs, timeout_seconds)
    cutoff_ms = int(datetime.now().timestamp() * 1000) - last_minutes * 60 * 1000

    query = {
        "size": limit * 3,  # 多取一些，因为需要去重到 trace 级别
        "query": {
            "bool": {
                "should": [
                    {"range": {"timestamp": {"gte": cutoff_ms}}},
                    {"range": {"@timestamp": {"gte": f"now-{last_minutes}m", "lte": "now"}}},
                ],
                "minimum_should_match": 1,
            }
        },
        "sort": [
            {"timestamp": {"order": "desc", "unmapped_type": "date"}},
            {"@timestamp": {"order": "desc", "unmapped_type": "date"}},
        ],
    }

    try:
        resp = es.search(index=index, body=query)
    except Exception as exc:
        raise ESQueryError(f"Failed to query recent traces: {exc}") from exc

    hits = resp.get("hits", {}).get("hits", [])

    # 按 trace_id 分组，每条 trace 取最新的一条 span
    traces_map: Dict[str, Dict[str, Any]] = {}
    for hit in hits:
        src = hit.get("_source", {})
        trace_id = str(src.get("trace_id") or src.get("traceId") or "")
        if not trace_id:
            continue

        if trace_id in traces_map:
            continue  # 已存在则跳过

        ts_val = src.get("timestamp") or src.get("@timestamp") or 0
        if isinstance(ts_val, str):
            try:
                ts_val = datetime.fromisoformat(ts_val.replace("Z", "+00:00")).timestamp() * 1000
            except Exception:
                ts_val = 0

        status = str(src.get("status") or "OK").lower()
        if status not in ("error", "failed", "degraded"):
            status = "ok"

        duration_ms = int(src.get("duration_ms") or src.get("durationMs") or src.get("duration") or 0)
        service_name = str(src.get("service_name") or src.get("serviceName") or "unknown")

        traces_map[trace_id] = {
            "run_id": f"es_{trace_id[:12]}",
            "trace_id": trace_id,
            "status": status,
            "started_at": datetime.fromtimestamp(float(ts_val) / 1000).isoformat() if ts_val else None,
            "duration_ms": duration_ms,
            "service_name": service_name,
        }

    result = list(traces_map.values())
    result.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return result[:limit]
