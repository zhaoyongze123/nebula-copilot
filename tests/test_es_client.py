from nebula_copilot.es_client import ESQueryError, trace_from_es_source


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
