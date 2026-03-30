#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from elasticsearch import Elasticsearch
from elasticsearch import helpers as es_helpers

SERVICES: List[Tuple[str, str, str]] = [
    ("gateway-service", "HTTP GET /api/v1/order/confirm", "GET"),
    ("user-service", "RPC getUserProfile", "RPC"),
    ("cart-service", "RPC getCartItems", "RPC"),
    ("inventory-service", "RPC lockStock", "RPC"),
    ("pricing-service", "RPC calculatePromotion", "RPC"),
    ("order-service", "RPC createOrder", "RPC"),
    ("payment-service", "HTTP POST /payment/charge", "POST"),
]

BASE_HEAP_MB: Dict[str, int] = {
    "gateway-service": 1024,
    "user-service": 1024,
    "cart-service": 1024,
    "inventory-service": 2048,
    "pricing-service": 1024,
    "order-service": 2048,
    "payment-service": 3072,
}

ISSUES: List[Dict[str, Any]] = [
    {
        "name": "timeout-payment",
        "service": "payment-service",
        "status": "ERROR",
        "duration_range": (1200, 2600),
        "error_type": "Timeout",
        "error_code": "ERR_TIMEOUT",
        "exception": "java.net.SocketTimeoutException: Read timed out\\n\\tat okhttp3.RealCall.timeoutExit(RealCall.kt:398)",
        "message": "downstream timeout while charging payment",
    },
    {
        "name": "db-deadlock-order",
        "service": "order-service",
        "status": "ERROR",
        "duration_range": (900, 2200),
        "error_type": "DB",
        "error_code": "ERR_DB_DEADLOCK",
        "exception": "org.springframework.dao.DeadlockLoserDataAccessException: Deadlock found when trying to get lock",
        "message": "order transaction deadlock retry exhausted",
    },
    {
        "name": "downstream-503-inventory",
        "service": "inventory-service",
        "status": "ERROR",
        "duration_range": (1000, 2400),
        "error_type": "Downstream",
        "error_code": "ERR_DOWNSTREAM_503",
        "exception": "feign.FeignException$ServiceUnavailable: [503] during [POST] to [http://stock-service/lock]",
        "message": "inventory downstream 503 when lock stock",
    },
    {
        "name": "unknown-npe-user",
        "service": "user-service",
        "status": "ERROR",
        "duration_range": (800, 1600),
        "error_type": "Unknown",
        "error_code": "ERR_NPE",
        "exception": "java.lang.NullPointerException: Cannot invoke String.length because token is null",
        "message": "null pointer in auth token parsing",
    },
    {
        "name": "slow-pricing-noerror",
        "service": "pricing-service",
        "status": "OK",
        "duration_range": (1100, 2600),
        "error_type": "None",
        "error_code": "WARN_SLOW_CALL",
        "exception": None,
        "message": "slow call detected in promotion engine",
    },
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write diverse issue traces into Elasticsearch.")
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--index", default="nebula_metrics")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--verify-certs", action="store_true")
    parser.add_argument("--no-verify-certs", dest="verify_certs", action="store_false")
    parser.set_defaults(verify_certs=False)
    parser.add_argument("--traces-per-issue", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20260329)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--refresh", default="wait_for", choices=["false", "true", "wait_for"])
    return parser.parse_args()


def _build_es(args: argparse.Namespace) -> Elasticsearch:
    auth = (args.username, args.password) if args.username and args.password else None
    return Elasticsearch(
        hosts=[args.es_url],
        basic_auth=auth,
        verify_certs=args.verify_certs,
        request_timeout=30,
    )


def _build_trace_docs(trace_id: str, issue: Dict[str, Any], ts_ms: int, rng: random.Random) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    root_span_id = uuid.uuid4().hex[:16]
    root_status = "ERROR" if issue["status"] == "ERROR" else "OK"
    root_duration = rng.randint(1800, 4200)

    root_doc = {
        "traceId": trace_id,
        "trace_id": trace_id,
        "spanId": root_span_id,
        "span_id": root_span_id,
        "parentSpanId": None,
        "parent_span_id": None,
        "serviceName": "trace-root",
        "service_name": "trace-root",
        "operationName": f"trace:{trace_id}",
        "operation_name": f"trace:{trace_id}",
        "methodName": f"trace:{trace_id}",
        "duration": root_duration,
        "durationMs": root_duration,
        "duration_ms": root_duration,
        "status": root_status,
        "exceptionStack": None,
        "exception_stack": None,
        "timestamp": ts_ms,
        "@timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
        "message": f"scenario={issue['name']}",
        "log": f"scenario={issue['name']}",
        "logLevel": "WARN",
        "heap_used_mb": 256.0,
        "heap_max_mb": 1024.0,
        "gc_count": rng.randint(0, 2),
        "thread_count": rng.randint(8, 24),
        "jvm": {"heap": {"used": 256.0, "max": 1024.0}, "gc": {"count": 1}, "threads": {"live": 16}},
        "tags": {"seed": "diverse-issues", "scenario": issue["name"]},
    }
    docs.append(root_doc)

    parent = root_span_id
    cur_ms = ts_ms
    for service_name, operation_name, method in SERVICES:
        cur_ms += rng.randint(3, 28)
        span_id = uuid.uuid4().hex[:16]
        is_target = service_name == issue["service"]

        duration = rng.randint(20, 180)
        status = "OK"
        exception = None
        error_type = "None"
        error_code = ""
        message = "request finished successfully"
        level = "INFO"

        if is_target:
            duration = rng.randint(*issue["duration_range"])
            status = issue["status"]
            exception = issue["exception"]
            error_type = issue["error_type"]
            error_code = issue["error_code"]
            message = issue["message"]
            level = "ERROR" if status == "ERROR" else "WARN"

        heap_max = float(BASE_HEAP_MB[service_name])
        heap_used = round(heap_max * rng.uniform(0.45, 0.72), 1)
        gc_count = rng.randint(1, 6)
        thread_count = rng.randint(40, 180)
        if is_target:
            heap_used = round(heap_max * rng.uniform(0.72, 0.93), 1)
            gc_count = rng.randint(8, 20)
            thread_count = rng.randint(120, 280)

        doc = {
            "traceId": trace_id,
            "trace_id": trace_id,
            "spanId": span_id,
            "span_id": span_id,
            "parentSpanId": parent,
            "parent_span_id": parent,
            "serviceName": service_name,
            "service_name": service_name,
            "operationName": operation_name,
            "operation_name": operation_name,
            "methodName": operation_name,
            "duration": duration,
            "durationMs": duration,
            "duration_ms": duration,
            "status": status,
            "exceptionStack": exception,
            "exception_stack": exception,
            "timestamp": cur_ms,
            "@timestamp": datetime.fromtimestamp(cur_ms / 1000, tz=timezone.utc).isoformat(),
            "httpMethod": method,
            "httpStatus": 500 if status == "ERROR" else 200,
            "errorType": error_type,
            "errorCode": error_code,
            "message": message,
            "log": message,
            "logLevel": level,
            "ai_diagnosis": f"scenario={issue['name']}",
            "heap_used_mb": heap_used,
            "heap_max_mb": heap_max,
            "gc_count": gc_count,
            "thread_count": thread_count,
            "jvm": {
                "heap": {"used": heap_used, "max": heap_max},
                "gc": {"count": gc_count, "pause_ms": round(rng.uniform(3.0, 65.0), 2)},
                "threads": {"live": thread_count},
            },
            "tags": {"seed": "diverse-issues", "scenario": issue["name"], "issue_service": issue["service"]},
        }
        docs.append(doc)
        parent = span_id

    return docs


def main() -> None:
    args = _parse_args()
    rng = random.Random(args.seed)
    es = _build_es(args)

    now = datetime.now(tz=timezone.utc)
    actions: List[Dict[str, Any]] = []
    samples: Dict[str, List[str]] = {issue["name"]: [] for issue in ISSUES}

    for issue in ISSUES:
        for _ in range(args.traces_per_issue):
            trace_id = uuid.uuid4().hex[:16]
            if len(samples[issue["name"]]) < 3:
                samples[issue["name"]].append(trace_id)
            ts = now - timedelta(seconds=rng.randint(1, max(60, args.minutes * 60)))
            ts_ms = int(ts.timestamp() * 1000)
            docs = _build_trace_docs(trace_id, issue, ts_ms, rng)
            for doc in docs:
                actions.append({"_index": args.index, "_source": doc})

    success, errors = es_helpers.bulk(
        es,
        actions,
        chunk_size=1000,
        request_timeout=120,
        stats_only=True,
        refresh=args.refresh,
    )

    total_traces = len(ISSUES) * args.traces_per_issue
    print("[DONE] diverse issue traces indexed")
    print(f"index={args.index}")
    print(f"traces={total_traces} docs={len(actions)} success={int(success)} errors={int(errors)}")
    for issue in ISSUES:
        name = issue["name"]
        print(f"sample_{name}={','.join(samples[name])}")


if __name__ == "__main__":
    main()
