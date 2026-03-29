from nebula_copilot.es_client import ESQueryError, query_service_jvm_metrics, search_service_logs, trace_from_es_source


def test_trace_from_es_nested_schema() -> None:
    source = {
        "trace_id": "trace_nested_1",
        "root": {
            "span_id": "s1",
            "service_name": "gateway",
            "operation_name": "GET /api",
            "duration_ms": 100,
            "status": "OK",
            "children": [
                {
                    "span_id": "s2",
                    "parent_span_id": "s1",
                    "service_name": "order",
                    "operation_name": "POST /orders",
                    "duration_ms": 300,
                    "status": "ERROR",
                    "exception_stack": "timeout",
                    "children": [],
                }
            ],
        },
    }

    trace = trace_from_es_source(source)
    assert trace.trace_id == "trace_nested_1"
    assert trace.root.children[0].service_name == "order"


def test_trace_from_es_flat_schema() -> None:
    source = {
        "traceId": "trace_flat_1",
        "spans": [
            {
                "spanId": "a",
                "parentSpanId": None,
                "serviceName": "frontend",
                "operationName": "checkout",
                "durationMs": 90,
                "status": "OK",
            },
            {
                "spanId": "b",
                "parentSpanId": "a",
                "serviceName": "inventory",
                "operationName": "reserve",
                "durationMs": 500,
                "status": "ERROR",
                "exceptionStack": "Read timed out",
            },
        ],
    }

    trace = trace_from_es_source(source)
    assert trace.trace_id == "trace_flat_1"
    assert trace.root.service_name == "frontend"
    assert trace.root.children[0].service_name == "inventory"


def test_trace_from_es_invalid_schema() -> None:
    bad = {"trace_id": "x"}
    try:
        trace_from_es_source(bad)
        assert False, "expected ESQueryError"
    except ESQueryError:
        assert True


def test_query_service_jvm_metrics_from_es(monkeypatch) -> None:
    class FakeES:
        def search(self, index: str, body: dict) -> dict:
            assert index == "nebula_metrics"
            return {
                "hits": {
                    "total": {"value": 12},
                    "hits": [
                        {
                            "_source": {
                                "service_name": "inventory-service",
                                "heap_used_mb": 768,
                                "heap_max_mb": 2048,
                                "gc_count": 9,
                                "thread_count": 122,
                                "@timestamp": "2026-03-29T12:00:00Z",
                            }
                        }
                    ],
                },
                "aggregations": {
                    "error_docs": {"doc_count": 3},
                    "p95_duration_ms": {"values": {"95.0": 1320.0}},
                    "p95_duration": {"values": {"95.0": None}},
                },
            }

    monkeypatch.setattr("nebula_copilot.es_client._build_es", lambda *args, **kwargs: FakeES())

    result = query_service_jvm_metrics(
        es_url="http://localhost:9200",
        index="nebula_metrics",
        service_name="inventory-service",
        last_minutes=15,
    )

    assert result["status"] == "ok"
    assert result["doc_count"] == 12
    assert result["heap_used_mb"] == 768.0
    assert result["p95_duration_ms"] == 1320.0
    assert result["error_count"] == 3
    assert result["error_rate"] == 0.25


def test_search_service_logs_from_es(monkeypatch) -> None:
    class FakeES:
        def search(self, index: str, body: dict) -> dict:
            assert index == "nebula_metrics"
            return {
                "hits": {
                    "total": {"value": 2},
                    "hits": [
                        {
                            "_source": {
                                "trace_id": "t-1",
                                "exceptionStack": "java.net.SocketTimeoutException: Read timed out",
                            }
                        },
                        {
                            "_source": {
                                "trace_id": "t-2",
                                "message": "downstream retry exhausted",
                            }
                        },
                    ],
                }
            }

    monkeypatch.setattr("nebula_copilot.es_client._build_es", lambda *args, **kwargs: FakeES())

    result = search_service_logs(
        es_url="http://localhost:9200",
        index="nebula_metrics",
        service_name="inventory-service",
        keyword="timeout",
        last_minutes=15,
        limit=5,
    )

    assert result["status"] == "ok"
    assert result["doc_count"] == 2
    assert len(result["sample"]) == 2
    assert result["trace_ids"] == ["t-1", "t-2"]


def test_query_service_jvm_metrics_supports_integer_hits_total(monkeypatch) -> None:
    class FakeES:
        def search(self, index: str, body: dict) -> dict:
            return {
                "hits": {
                    "total": 4,
                    "hits": [
                        {
                            "_source": {
                                "service_name": "order-service",
                                "heap_used_mb": 512,
                                "heap_max_mb": 1024,
                            }
                        }
                    ],
                },
                "aggregations": {
                    "error_docs": {"doc_count": 1},
                    "p95_duration_ms": {"values": {"95.0": 880.0}},
                    "p95_duration": {"values": {"95.0": None}},
                },
            }

    monkeypatch.setattr("nebula_copilot.es_client._build_es", lambda *args, **kwargs: FakeES())
    result = query_service_jvm_metrics(
        es_url="http://localhost:9200",
        index="nebula_metrics",
        service_name="order-service",
        last_minutes=10,
    )

    assert result["status"] == "ok"
    assert result["doc_count"] == 4
    assert result["error_rate"] == 0.25
