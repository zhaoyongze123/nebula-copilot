from __future__ import annotations

import json
from pathlib import Path

from nebula_copilot.models import Span, TraceDocument

DEFAULT_TRACE_ID = "trace_mock_2026_0001"


def _timeout_trace(trace_id: str) -> TraceDocument:
    inventory_span = Span(
        span_id="span_inventory",
        parent_span_id="span_order",
        service_name="inventory-service",
        operation_name="GET /inventory/reserve",
        duration_ms=1280,
        status="ERROR",
        exception_stack=(
            "java.net.SocketTimeoutException: Read timed out\n"
            "\tat com.inventory.client.HttpClient.call(HttpClient.java:87)\n"
            "\tat com.inventory.service.InventoryService.reserve(InventoryService.java:142)\n"
            "\t... 12 more"
        ),
        children=[],
    )

    order_span = Span(
        span_id="span_order",
        parent_span_id="span_gateway",
        service_name="order-service",
        operation_name="POST /orders/create",
        duration_ms=1080,
        status="ERROR",
        exception_stack=None,
        children=[inventory_span],
    )

    gateway_span = Span(
        span_id="span_gateway",
        parent_span_id="span_frontend",
        service_name="api-gateway",
        operation_name="POST /api/orders",
        duration_ms=980,
        status="ERROR",
        exception_stack=None,
        children=[order_span],
    )

    root_span = Span(
        span_id="span_frontend",
        parent_span_id=None,
        service_name="frontend-web",
        operation_name="User checkout",
        duration_ms=920,
        status="ERROR",
        exception_stack=None,
        children=[gateway_span],
    )

    return TraceDocument(trace_id=trace_id, root=root_span)


def _db_deadlock_trace(trace_id: str) -> TraceDocument:
    order_span = Span(
        span_id="span_order_db",
        parent_span_id="span_gateway_db",
        service_name="order-service",
        operation_name="POST /orders/create",
        duration_ms=1420,
        status="ERROR",
        exception_stack=(
            "org.springframework.dao.DeadlockLoserDataAccessException: Deadlock found when trying to get lock\n"
            "\tat com.order.repository.OrderRepository.save(OrderRepository.java:55)\n"
            "\tat com.order.service.OrderService.create(OrderService.java:91)"
        ),
        children=[],
    )
    gateway = Span(
        span_id="span_gateway_db",
        parent_span_id="span_frontend_db",
        service_name="api-gateway",
        operation_name="POST /api/orders",
        duration_ms=990,
        status="ERROR",
        exception_stack=None,
        children=[order_span],
    )
    root = Span(
        span_id="span_frontend_db",
        parent_span_id=None,
        service_name="frontend-web",
        operation_name="User checkout",
        duration_ms=930,
        status="ERROR",
        exception_stack=None,
        children=[gateway],
    )
    return TraceDocument(trace_id=trace_id, root=root)


def _downstream_trace(trace_id: str) -> TraceDocument:
    payment = Span(
        span_id="span_payment",
        parent_span_id="span_order_pay",
        service_name="payment-service",
        operation_name="POST /payment/charge",
        duration_ms=1510,
        status="ERROR",
        exception_stack=(
            "java.io.IOException: downstream service unavailable, status=503\n"
            "\tat com.payment.client.BankClient.charge(BankClient.java:66)\n"
            "\tCaused by: java.net.ConnectException: Connection refused"
        ),
        children=[],
    )
    order = Span(
        span_id="span_order_pay",
        parent_span_id="span_gateway_pay",
        service_name="order-service",
        operation_name="POST /orders/create",
        duration_ms=1180,
        status="ERROR",
        exception_stack=None,
        children=[payment],
    )
    gateway = Span(
        span_id="span_gateway_pay",
        parent_span_id="span_frontend_pay",
        service_name="api-gateway",
        operation_name="POST /api/orders",
        duration_ms=1000,
        status="ERROR",
        exception_stack=None,
        children=[order],
    )
    root = Span(
        span_id="span_frontend_pay",
        parent_span_id=None,
        service_name="frontend-web",
        operation_name="User checkout",
        duration_ms=940,
        status="ERROR",
        exception_stack=None,
        children=[gateway],
    )
    return TraceDocument(trace_id=trace_id, root=root)


def build_mock_trace(trace_id: str = DEFAULT_TRACE_ID, scenario: str = "timeout") -> TraceDocument:
    scenario = scenario.lower()
    if scenario == "timeout":
        return _timeout_trace(trace_id)
    if scenario == "db":
        return _db_deadlock_trace(trace_id)
    if scenario == "downstream":
        return _downstream_trace(trace_id)
    raise ValueError(f"Unsupported scenario: {scenario}")


def write_mock_file(
    output_path: Path,
    trace_id: str = DEFAULT_TRACE_ID,
    scenario: str = "timeout",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trace_doc = build_mock_trace(trace_id, scenario)
    output_path.write_text(trace_doc.model_dump_json(indent=2, ensure_ascii=False))
    return output_path


def load_mock_file(path: Path) -> TraceDocument:
    payload = json.loads(path.read_text())
    return TraceDocument.model_validate(payload)
