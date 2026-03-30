#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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

BASE_DURATION_MS = {
    "gateway-service": (20, 80),
    "user-service": (8, 40),
    "cart-service": (12, 60),
    "inventory-service": (15, 90),
    "pricing-service": (10, 70),
    "order-service": (20, 120),
    "payment-service": (30, 160),
}

BASE_HEAP_MAX_MB = {
    "gateway-service": 1024,
    "user-service": 1024,
    "cart-service": 1024,
    "inventory-service": 2048,
    "pricing-service": 1024,
    "order-service": 2048,
    "payment-service": 3072,
}

BASE_THREAD_RANGE = {
    "gateway-service": (80, 220),
    "user-service": (40, 120),
    "cart-service": (40, 130),
    "inventory-service": (60, 180),
    "pricing-service": (40, 140),
    "order-service": (70, 200),
    "payment-service": (90, 260),
}

ERROR_STACKS = {
    "Timeout": "java.net.SocketTimeoutException: Read timed out\n"
    "\tat okhttp3.internal.connection.RealCall.timeoutExit(RealCall.kt:398)\n"
    "\tat com.nebula.payment.client.PaymentClient.charge(PaymentClient.java:128)",
    "DB": "org.springframework.dao.DeadlockLoserDataAccessException: Deadlock found when trying to get lock\n"
    "\tat com.mysql.cj.jdbc.ClientPreparedStatement.executeInternal(ClientPreparedStatement.java:953)\n"
    "\tat com.nebula.order.repository.OrderRepository.save(OrderRepository.java:77)",
    "Downstream": "feign.FeignException$ServiceUnavailable: [503] during [POST] to [http://payment-service/charge]\n"
    "\tat feign.FeignException.errorStatus(FeignException.java:249)\n"
    "\tat com.nebula.order.client.PaymentFeign.charge(PaymentFeign.java:44)",
    "Redis": "redis.clients.jedis.exceptions.JedisConnectionException: Failed connecting to redis-cache:6379\n"
    "\tat redis.clients.jedis.Connection.connect(Connection.java:230)\n"
    "\tat com.nebula.cart.cache.RedisCartStore.get(RedisCartStore.java:61)",
    "CircuitOpen": "io.github.resilience4j.circuitbreaker.CallNotPermittedException: CircuitBreaker 'payment-service' is OPEN\n"
    "\tat io.github.resilience4j.circuitbreaker.CircuitBreaker.decorateCallable(CircuitBreaker.java:180)\n"
    "\tat com.nebula.order.client.PaymentGatewayClient.charge(PaymentGatewayClient.java:52)",
    "NullPointer": "java.lang.NullPointerException: Cannot invoke \"OrderContext.getUserId()\" because \"ctx\" is null\n"
    "\tat com.nebula.order.service.OrderSubmitService.buildRequest(OrderSubmitService.java:142)\n"
    "\tat com.nebula.order.service.OrderSubmitService.submit(OrderSubmitService.java:88)",
}


@dataclass
class TraceBlueprint:
    trace_id: str
    kind: str  # normal | slow | error
    timestamp_ms: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate high-fidelity simulated distributed tracing data and bulk index to Elasticsearch."
    )
    parser.add_argument("--es-url", default="http://localhost:9200", help="Elasticsearch URL")
    parser.add_argument("--index", default="nebula_metrics", help="Target index")
    parser.add_argument("--username", default=None, help="ES username")
    parser.add_argument("--password", default=None, help="ES password")
    parser.add_argument("--verify-certs", action="store_true", help="Enable TLS cert verification")
    parser.add_argument("--no-verify-certs", dest="verify_certs", action="store_false", help="Disable TLS cert verification")
    parser.set_defaults(verify_certs=False)

    parser.add_argument("--traces", type=int, default=500, help="Total number of traces to generate")
    parser.add_argument("--batch-size", type=int, default=1000, help="Bulk write chunk size")
    parser.add_argument("--time-window-minutes", type=int, default=60, help="Spread data across last N minutes")
    parser.add_argument("--seed", type=int, default=20260329, help="Random seed")

    parser.add_argument("--normal-ratio", type=float, default=0.75, help="Normal trace ratio")
    parser.add_argument("--slow-ratio", type=float, default=0.2, help="Slow trace ratio")
    parser.add_argument("--error-ratio", type=float, default=0.05, help="Error trace ratio")

    parser.add_argument("--create-index", action="store_true", help="Create index with mapping when missing")
    parser.add_argument("--reset-index", action="store_true", help="Delete and recreate index before writing")
    parser.add_argument("--refresh", default="false", choices=["false", "true", "wait_for"], help="Refresh policy after bulk")
    return parser.parse_args()


def _build_es_client(args: argparse.Namespace) -> Elasticsearch:
    auth = (args.username, args.password) if args.username and args.password else None
    return Elasticsearch(
        hosts=[args.es_url],
        basic_auth=auth,
        verify_certs=args.verify_certs,
        request_timeout=30,
    )


def _index_mapping() -> Dict[str, Any]:
    return {
        "mappings": {
            "properties": {
                "traceId": {"type": "keyword"},
                "trace_id": {"type": "keyword"},
                "spanId": {"type": "keyword"},
                "span_id": {"type": "keyword"},
                "parentSpanId": {"type": "keyword"},
                "parent_span_id": {"type": "keyword"},
                "serviceName": {"type": "keyword"},
                "service_name": {"type": "keyword"},
                "methodName": {"type": "keyword"},
                "operationName": {"type": "keyword"},
                "operation_name": {"type": "keyword"},
                "status": {"type": "keyword"},
                "duration": {"type": "long"},
                "durationMs": {"type": "long"},
                "duration_ms": {"type": "long"},
                "timestamp": {"type": "date", "format": "epoch_millis"},
                "@timestamp": {"type": "date"},
                "environment": {"type": "keyword"},
                "region": {"type": "keyword"},
                "cluster": {"type": "keyword"},
                "instanceId": {"type": "keyword"},
                "podName": {"type": "keyword"},
                "httpStatus": {"type": "integer"},
                "errorType": {"type": "keyword"},
                "errorCode": {"type": "keyword"},
                "exceptionStack": {"type": "text"},
                "exception_stack": {"type": "text"},
                "message": {"type": "text"},
                "log": {"type": "text"},
                "logLevel": {"type": "keyword"},
                "heap_used_mb": {"type": "float"},
                "heap_max_mb": {"type": "float"},
                "gc_count": {"type": "integer"},
                "thread_count": {"type": "integer"},
                "cpu_usage": {"type": "float"},
                "memory_rss_mb": {"type": "float"},
                "jvm": {"type": "object", "enabled": True},
                "tags": {"type": "object", "enabled": True},
            }
        }
    }


def _ensure_index(es: Elasticsearch, index: str, reset: bool, create: bool) -> None:
    exists = es.indices.exists(index=index)
    if reset and exists:
        es.indices.delete(index=index)
        exists = False
    if create and not exists:
        es.indices.create(index=index, body=_index_mapping())


def _choose_kind(rng: random.Random, normal_ratio: float, slow_ratio: float, error_ratio: float) -> str:
    p = rng.random()
    if p < normal_ratio:
        return "normal"
    if p < normal_ratio + slow_ratio:
        return "slow"
    return "error"


def _trace_timestamp_ms(rng: random.Random, now: datetime, window_minutes: int) -> int:
    # Bias toward recent events while still covering the full window.
    span_seconds = window_minutes * 60
    offset = int((rng.random() ** 2) * span_seconds)
    ts = now - timedelta(seconds=offset)
    return int(ts.timestamp() * 1000)


def _random_http_status(kind: str, rng: random.Random) -> int:
    if kind == "normal":
        return rng.choice([200, 200, 200, 201])
    if kind == "slow":
        return rng.choice([200, 200, 504])
    return rng.choice([500, 502, 503, 504])


def _error_profile(rng: random.Random) -> Tuple[str, str, str]:
    error_type = rng.choice(list(ERROR_STACKS.keys()))
    stack = ERROR_STACKS[error_type]
    code = {
        "Timeout": "ERR_TIMEOUT",
        "DB": "ERR_DB_DEADLOCK",
        "Downstream": "ERR_DOWNSTREAM_503",
        "Redis": "ERR_REDIS_CONN",
        "CircuitOpen": "ERR_CIRCUIT_OPEN",
        "NullPointer": "ERR_NPE",
    }[error_type]
    return error_type, code, stack


def _build_trace_docs(blueprint: TraceBlueprint, rng: random.Random) -> List[Dict[str, Any]]:
    trace_id = blueprint.trace_id
    root_ts = blueprint.timestamp_ms
    docs: List[Dict[str, Any]] = []

    span_ids: List[str] = [uuid.uuid4().hex[:16] for _ in range(len(SERVICES))]
    durations: List[int] = []
    statuses: List[str] = ["OK"] * len(SERVICES)
    exception_stack: List[Optional[str]] = [None] * len(SERVICES)
    error_type: List[str] = ["None"] * len(SERVICES)
    error_code: List[str] = [""] * len(SERVICES)
    stressed_index: Optional[int] = None

    for service_name, _, _ in SERVICES:
        low, high = BASE_DURATION_MS[service_name]
        durations.append(rng.randint(low, high))

    if blueprint.kind == "slow":
        idx = rng.randint(2, len(SERVICES) - 1)
        stressed_index = idx
        durations[idx] = int(durations[idx] * rng.uniform(8.0, 18.0))
        error_type[idx] = "SlowCall"
        error_code[idx] = "WARN_SLOW_CALL"
    elif blueprint.kind == "error":
        idx = rng.randint(2, len(SERVICES) - 1)
        stressed_index = idx
        et, ec, stack = _error_profile(rng)
        statuses[idx] = "ERROR"
        exception_stack[idx] = stack
        error_type[idx] = et
        error_code[idx] = ec
        durations[idx] = int(durations[idx] * rng.uniform(3.0, 10.0))

    if blueprint.kind == "slow":
        total_duration = sum(durations) + rng.randint(80, 250)
    elif blueprint.kind == "error":
        total_duration = sum(durations) + rng.randint(40, 160)
    else:
        total_duration = sum(durations) + rng.randint(20, 80)

    current_ts = root_ts
    root_span_id = uuid.uuid4().hex[:16]
    root_status = "ERROR" if "ERROR" in statuses else "OK"
    root_doc = {
        "traceId": trace_id,
        "trace_id": trace_id,
        "spanId": root_span_id,
        "span_id": root_span_id,
        "parentSpanId": None,
        "parent_span_id": None,
        "serviceName": "trace-root",
        "service_name": "trace-root",
        "methodName": f"trace:{trace_id}",
        "operationName": f"trace:{trace_id}",
        "operation_name": f"trace:{trace_id}",
        "duration": total_duration,
        "durationMs": total_duration,
        "duration_ms": total_duration,
        "status": root_status,
        "exceptionStack": None,
        "exception_stack": None,
        "timestamp": root_ts,
        "@timestamp": datetime.fromtimestamp(root_ts / 1000, tz=timezone.utc).isoformat(),
        "sampled": True,
        "environment": "prod",
        "region": rng.choice(["cn-shanghai", "cn-beijing", "cn-hangzhou"]),
        "cluster": "nebula-prod-a",
        "namespace": "nebula",
        "instanceId": "root-trace-collector",
        "podName": "trace-root-collector-0",
        "thread": "trace-worker-1",
        "httpMethod": "TRACE",
        "httpStatus": 200 if root_status == "OK" else 500,
        "errorType": "None" if root_status == "OK" else "RootError",
        "errorCode": "",
        "message": (
            "trace pipeline detected slow branch"
            if blueprint.kind == "slow"
            else ("trace pipeline detected error branch" if blueprint.kind == "error" else "trace completed normally")
        ),
        "log": (
            "trace pipeline detected slow branch"
            if blueprint.kind == "slow"
            else ("trace pipeline detected error branch" if blueprint.kind == "error" else "trace completed normally")
        ),
        "logLevel": "ERROR" if blueprint.kind == "error" else ("WARN" if blueprint.kind == "slow" else "INFO"),
        "heap_used_mb": 256.0,
        "heap_max_mb": 1024.0,
        "gc_count": rng.randint(0, 2),
        "thread_count": rng.randint(8, 24),
        "cpu_usage": round(rng.uniform(0.08, 0.22), 3),
        "memory_rss_mb": round(rng.uniform(300.0, 520.0), 1),
        "jvm": {
            "heap": {"used": 256.0, "max": 1024.0},
            "gc": {"count": rng.randint(0, 2), "pause_ms": round(rng.uniform(1.0, 8.0), 2)},
            "threads": {"live": rng.randint(8, 24)},
        },
        "alertLevel": "critical" if blueprint.kind == "error" else ("warning" if blueprint.kind == "slow" else "normal"),
        "tags": {
            "bizLine": rng.choice(["order", "fulfillment", "checkout"]),
            "tenant": rng.choice(["vip", "enterprise", "default"]),
            "host": f"trace-host-{rng.randint(1, 6)}",
        },
    }
    docs.append(root_doc)

    parent = root_span_id
    for i, (service_name, operation_name, http_method) in enumerate(SERVICES):
        current_ts += rng.randint(1, 30)
        status = statuses[i]
        exc = exception_stack[i]
        et = error_type[i]
        ec = error_code[i]

        heap_max_mb = float(BASE_HEAP_MAX_MB[service_name])
        heap_used_ratio = rng.uniform(0.35, 0.68)
        gc_count = rng.randint(0, 6)
        thread_low, thread_high = BASE_THREAD_RANGE[service_name]
        thread_count = rng.randint(thread_low, thread_high)
        cpu_usage = round(rng.uniform(0.12, 0.56), 3)
        memory_rss_mb = round(heap_max_mb * rng.uniform(0.58, 0.92), 1)

        if stressed_index is not None and i == stressed_index:
            if blueprint.kind == "slow":
                heap_used_ratio = rng.uniform(0.75, 0.9)
                gc_count = rng.randint(8, 22)
                thread_count = int(thread_high * rng.uniform(0.85, 1.08))
                cpu_usage = round(rng.uniform(0.62, 0.91), 3)
            elif blueprint.kind == "error":
                heap_used_ratio = rng.uniform(0.7, 0.92)
                gc_count = rng.randint(5, 18)
                thread_count = int(thread_high * rng.uniform(0.8, 1.05))
                cpu_usage = round(rng.uniform(0.55, 0.88), 3)

        heap_used_mb = round(heap_max_mb * heap_used_ratio, 1)
        jvm_pause_ms = round(rng.uniform(2.0, 45.0) * (1.7 if i == stressed_index else 1.0), 2)

        if status == "ERROR":
            log_level = "ERROR"
            message = (exc or "unexpected internal error").split("\n", 1)[0]
        elif blueprint.kind == "slow" and i == stressed_index:
            log_level = "WARN"
            message = f"slow call detected, duration={durations[i]}ms exceeded service baseline"
        elif blueprint.kind == "normal":
            log_level = "INFO"
            message = "request finished successfully"
        else:
            log_level = "INFO"
            message = f"service call completed, duration={durations[i]}ms"

        diagnosis_hint = {
            "normal": "latency and jvm metrics stable",
            "slow": "suspect thread pool saturation or downstream latency spike",
            "error": "suspect dependency availability or transaction conflict",
        }[blueprint.kind]

        span_doc = {
            "traceId": trace_id,
            "trace_id": trace_id,
            "spanId": span_ids[i],
            "span_id": span_ids[i],
            "parentSpanId": parent,
            "parent_span_id": parent,
            "serviceName": service_name,
            "service_name": service_name,
            "methodName": operation_name,
            "operationName": operation_name,
            "operation_name": operation_name,
            "duration": durations[i],
            "durationMs": durations[i],
            "duration_ms": durations[i],
            "status": status,
            "exceptionStack": exc,
            "exception_stack": exc,
            "timestamp": current_ts,
            "@timestamp": datetime.fromtimestamp(current_ts / 1000, tz=timezone.utc).isoformat(),
            "sampled": True,
            "environment": "prod",
            "region": root_doc["region"],
            "cluster": "nebula-prod-a",
            "namespace": "nebula",
            "instanceId": f"{service_name}-{rng.randint(1, 12)}",
            "podName": f"{service_name}-{rng.randint(1000, 9999)}-{rng.choice(['a', 'b', 'c'])}",
            "thread": f"http-nio-{rng.randint(1, 16)}",
            "httpMethod": http_method,
            "httpPath": operation_name.split(" ", 1)[-1] if " " in operation_name else operation_name,
            "httpStatus": _random_http_status(blueprint.kind if status == "OK" else "error", rng),
            "errorType": et,
            "errorCode": ec,
            "message": message,
            "log": message,
            "logLevel": log_level,
            "ai_diagnosis": diagnosis_hint,
            "heap_used_mb": heap_used_mb,
            "heap_max_mb": heap_max_mb,
            "gc_count": gc_count,
            "thread_count": thread_count,
            "cpu_usage": cpu_usage,
            "memory_rss_mb": memory_rss_mb,
            "jvm": {
                "heap": {"used": heap_used_mb, "max": heap_max_mb},
                "gc": {"count": gc_count, "pause_ms": jvm_pause_ms},
                "threads": {"live": thread_count},
            },
            "upstreamService": "trace-root" if i == 0 else SERVICES[i - 1][0],
            "tags": {
                "az": rng.choice(["az1", "az2", "az3"]),
                "node": f"node-{rng.randint(1, 50)}",
                "containerImage": f"{service_name}:2026.03.{rng.randint(1, 29)}",
            },
        }
        docs.append(span_doc)
        parent = span_ids[i]

    return docs


def _generate_blueprints(args: argparse.Namespace, rng: random.Random) -> List[TraceBlueprint]:
    now = datetime.now(tz=timezone.utc)
    blueprints: List[TraceBlueprint] = []
    for _ in range(args.traces):
        tid = uuid.uuid4().hex[:16]
        kind = _choose_kind(rng, args.normal_ratio, args.slow_ratio, args.error_ratio)
        ts = _trace_timestamp_ms(rng, now, args.time_window_minutes)
        blueprints.append(TraceBlueprint(trace_id=tid, kind=kind, timestamp_ms=ts))
    return blueprints


def _validate_ratios(args: argparse.Namespace) -> None:
    values = [args.normal_ratio, args.slow_ratio, args.error_ratio]
    if any(v < 0 for v in values):
        raise ValueError("Ratios must be non-negative.")
    total = sum(values)
    if total <= 0:
        raise ValueError("At least one ratio must be > 0.")
    args.normal_ratio = args.normal_ratio / total
    args.slow_ratio = args.slow_ratio / total
    args.error_ratio = args.error_ratio / total


def _bulk_write(es: Elasticsearch, index: str, docs: List[Dict[str, Any]], batch_size: int, refresh: str) -> Tuple[int, int]:
    actions = ({"_index": index, "_source": doc} for doc in docs)
    success, errors = es_helpers.bulk(
        es,
        actions,
        chunk_size=batch_size,
        request_timeout=120,
        stats_only=True,
        refresh=refresh,
    )
    return int(success), int(errors)


def main() -> None:
    args = _parse_args()
    _validate_ratios(args)
    rng = random.Random(args.seed)

    es = _build_es_client(args)
    _ensure_index(es, args.index, reset=args.reset_index, create=args.create_index)

    blueprints = _generate_blueprints(args, rng)
    docs: List[Dict[str, Any]] = []
    kind_counter: Counter[str] = Counter()
    samples: Dict[str, List[str]] = defaultdict(list)

    for blueprint in blueprints:
        kind_counter[blueprint.kind] += 1
        if len(samples[blueprint.kind]) < 5:
            samples[blueprint.kind].append(blueprint.trace_id)
        docs.extend(_build_trace_docs(blueprint, rng))

    success, errors = _bulk_write(es, args.index, docs, args.batch_size, args.refresh)

    print("[DONE] bulk indexing completed")
    print(f"index={args.index}")
    print(f"traces={len(blueprints)} spans={len(docs)}")
    print(f"bulk_success={success} bulk_errors={errors}")
    print(
        "distribution="
        f"normal:{kind_counter['normal']} "
        f"slow:{kind_counter['slow']} "
        f"error:{kind_counter['error']}"
    )
    for kind in ("normal", "slow", "error"):
        if samples[kind]:
            print(f"sample_{kind}_trace_ids={','.join(samples[kind])}")


if __name__ == "__main__":
    main()
