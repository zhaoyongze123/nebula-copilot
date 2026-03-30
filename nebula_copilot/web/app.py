from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request

from nebula_copilot.analyzer import analyze_trace
from nebula_copilot.cli import _load_run_records
from nebula_copilot.errors import DataSourceError, TraceNotFoundError, TraceValidationError
from nebula_copilot.es_client import ESQueryError, fetch_trace_by_id, search_service_logs
from nebula_copilot.repository import LocalJsonRepository


MASK_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "webhook",
    "cookie",
    "set-cookie",
}


def _parse_iso(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _mask_sensitive(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for k, v in value.items():
            lk = k.lower()
            if lk in MASK_KEYS:
                masked[k] = "***"
            else:
                masked[k] = _mask_sensitive(v, key=k)
        return masked
    if isinstance(value, list):
        return [_mask_sensitive(item, key=key) for item in value]
    if isinstance(value, str) and key and key.lower() in MASK_KEYS:
        return "***"
    return value


def _envelope(data: Any, *, source: str, degraded: bool, start_ms: float, error: str | None = None):
    latency_ms = int((time.time() * 1000) - start_ms)
    return {
        "ok": error is None,
        "data": _mask_sensitive(data),
        "error": error,
        "meta": {
            "source": source,
            "degraded": degraded,
            "latency_ms": latency_ms,
        },
    }


def _load_runs(path: Path) -> list[dict[str, Any]]:
    records = [item for item in _load_run_records(path) if isinstance(item, dict)]
    normalized: list[dict[str, Any]] = []
    for item in records:
        view = dict(item)
        view["status"] = _normalized_run_status(item)
        normalized.append(view)
    return normalized


def _diagnosis_has_error(item: dict[str, Any]) -> bool:
    diagnosis = item.get("diagnosis")
    if not isinstance(diagnosis, dict):
        return False

    bottleneck = diagnosis.get("bottleneck")
    if isinstance(bottleneck, dict):
        status = str(bottleneck.get("status") or "").upper()
        if status == "ERROR":
            return True

    top_spans = diagnosis.get("top_spans")
    if isinstance(top_spans, list):
        for span in top_spans:
            if not isinstance(span, dict):
                continue
            if str(span.get("status") or "").upper() == "ERROR":
                return True
    return False


def _normalized_run_status(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").lower()
    if status != "failed":
        return status

    if _diagnosis_has_error(item):
        return status

    error_text = str(item.get("error") or "").lower()
    history = item.get("history") if isinstance(item.get("history"), list) else []
    has_llm_fallback = any(
        isinstance(ev, dict)
        and "llm" in str(ev.get("node") or "").lower()
        and str(ev.get("status") or "").lower() in {"fallback", "failed"}
        for ev in history
    )

    if has_llm_fallback or any(
        token in error_text
        for token in ["rate limit", "ratelimit", "429", "llm decision required", "openai"]
    ):
        return "degraded"

    return status


def _status_rank(status: str) -> int:
    order = {
        "failed": 0,
        "degraded": 1,
        "rate_limited": 2,
        "deduped": 3,
        "ok": 4,
        "skipped": 5,
    }
    return order.get((status or "").lower(), 99)


def _sort_runs(items: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "slowest":
        return sorted(
            items,
            key=lambda x: (
                -int((x.get("metrics") or {}).get("duration_ms") or 0),
                -_parse_iso(str(x.get("started_at") or "")),
                str(x.get("run_id") or ""),
            ),
        )
    if mode == "most_retries":
        return sorted(
            items,
            key=lambda x: (
                -int((x.get("notify") or {}).get("attempts") or 0),
                -_parse_iso(str(x.get("started_at") or "")),
                str(x.get("run_id") or ""),
            ),
        )
    if mode == "error_first":
        return sorted(
            items,
            key=lambda x: (
                _status_rank(str(x.get("status") or "")),
                -_parse_iso(str(x.get("started_at") or "")),
                str(x.get("run_id") or ""),
            ),
        )
    return sorted(
        items,
        key=lambda x: (
            -_parse_iso(str(x.get("started_at") or "")),
            str(x.get("run_id") or ""),
        ),
    )


def _span_to_dict(span: Any) -> dict[str, Any]:
    return {
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "service_name": span.service_name,
        "operation_name": span.operation_name,
        "duration_ms": span.duration_ms,
        "status": span.status,
        "exception_stack": span.exception_stack,
        "children": [_span_to_dict(child) for child in span.children],
    }


def _find_span(span: Any, span_id: str) -> Any | None:
    if span.span_id == span_id:
        return span
    for child in span.children:
        found = _find_span(child, span_id)
        if found is not None:
            return found
    return None


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    @app.get("/")
    def root() -> Any:
        return redirect("/dashboard")

    @app.get("/health")
    def health() -> Any:
        start = time.time() * 1000
        return jsonify(_envelope({"status": "ok"}, source="local", degraded=False, start_ms=start))

    @app.get("/dashboard")
    def dashboard() -> Any:
        return render_template("dashboard.html")

    @app.get("/api/overview")
    def api_overview() -> Any:
        start = time.time() * 1000
        runs_path = Path(request.args.get("runs_path", "data/agent_runs.json"))
        runs = _load_runs(runs_path)

        total = len(runs)
        failed = sum(1 for item in runs if str(item.get("status")) == "failed")
        degraded = sum(1 for item in runs if str(item.get("status")) == "degraded")
        ok = sum(1 for item in runs if str(item.get("status")) == "ok")
        success_rate = round((ok / total) * 100, 2) if total else 0.0

        durations = [int((item.get("metrics") or {}).get("duration_ms") or 0) for item in runs]
        durations = [d for d in durations if d > 0]
        p95 = 0
        if durations:
            durations.sort()
            idx = min(len(durations) - 1, max(0, int(len(durations) * 0.95) - 1))
            p95 = durations[idx]

        recent_anomalies = []
        for item in _sort_runs(runs, "error_first"):
            status = str(item.get("status") or "")
            if status in {"failed", "degraded"}:
                recent_anomalies.append(
                    {
                        "run_id": item.get("run_id"),
                        "trace_id": item.get("trace_id"),
                        "status": status,
                        "started_at": item.get("started_at"),
                    }
                )
            if len(recent_anomalies) >= 10:
                break

        data = {
            "kpi": {
                "total": total,
                "success_rate": success_rate,
                "failed": failed,
                "degraded": degraded,
                "p95_duration_ms": p95,
            },
            "recent_anomalies": recent_anomalies,
        }
        return jsonify(_envelope(data, source="local", degraded=False, start_ms=start))

    @app.get("/api/runs")
    def api_runs() -> Any:
        start = time.time() * 1000
        runs_path = Path(request.args.get("runs_path", "data/agent_runs.json"))
        status = request.args.get("status", "").strip()
        trace_id = request.args.get("trace_id", "").strip()
        sort_mode = request.args.get("sort", "error_first").strip() or "error_first"
        page = max(1, int(request.args.get("page", "1")))
        size = max(1, min(100, int(request.args.get("size", "20"))))

        items = _load_runs(runs_path)
        if status:
            items = [item for item in items if str(item.get("status") or "") == status]
        if trace_id:
            items = [item for item in items if str(item.get("trace_id") or "") == trace_id]

        items = _sort_runs(items, sort_mode)
        total = len(items)
        start_idx = (page - 1) * size
        end_idx = start_idx + size
        page_items = items[start_idx:end_idx]

        data = {
            "items": page_items,
            "paging": {
                "page": page,
                "size": size,
                "total": total,
                "has_next": end_idx < total,
            },
        }
        return jsonify(_envelope(data, source="local", degraded=False, start_ms=start))

    @app.get("/api/runs/<run_id>/page")
    def api_run_detail(run_id: str) -> Any:
        start = time.time() * 1000
        runs_path = Path(request.args.get("runs_path", "data/agent_runs.json"))
        items = _load_runs(runs_path)
        found = next((item for item in items if str(item.get("run_id") or "") == run_id), None)
        if found is None:
            return jsonify(_envelope({}, source="local", degraded=True, start_ms=start, error="run_id_not_found")), 404

        summary = {
            "run_id": found.get("run_id"),
            "trace_id": found.get("trace_id"),
            "status": found.get("status"),
            "started_at": found.get("started_at"),
            "finished_at": found.get("finished_at"),
            "duration_ms": (found.get("metrics") or {}).get("duration_ms"),
            "notify_status": (found.get("notify") or {}).get("status"),
        }

        data = {
            "summary": summary,
            "timeline": found.get("history") or [],
            "diagnosis": found.get("diagnosis") or {},
            "metrics": found.get("metrics") or {},
            "notify": found.get("notify") or {},
            "raw": found,
        }
        return jsonify(_envelope(data, source="local", degraded=False, start_ms=start))

    @app.get("/api/traces/<trace_id>/inspect")
    def api_trace_inspect(trace_id: str) -> Any:
        start = time.time() * 1000
        source = request.args.get("source", "auto")
        local_path = Path(request.args.get("local_path", "data/mock_trace.json"))
        es_url = request.args.get("es_url") or os.getenv("NEBULA_ES_URL", "http://localhost:9200")
        index = request.args.get("index") or os.getenv("NEBULA_ES_INDEX", "nebula_metrics")
        username = request.args.get("username") or os.getenv("NEBULA_ES_USERNAME")
        password = request.args.get("password") or os.getenv("NEBULA_ES_PASSWORD")
        verify_certs = request.args.get("verify_certs", "true").lower() == "true"
        timeout_seconds = int(request.args.get("timeout_seconds", "10"))

        try:
            source_name = source
            if source == "es":
                trace_doc = fetch_trace_by_id(
                    es_url=es_url,
                    index=index,
                    trace_id=trace_id,
                    username=username,
                    password=password,
                    verify_certs=verify_certs,
                    timeout_seconds=timeout_seconds,
                )
                source_name = "es"
            elif source == "local":
                trace_doc = LocalJsonRepository(local_path).get_trace(trace_id)
                source_name = "local"
            else:
                # Auto mode: prefer local for existing mock/debug flow, fallback to ES for real traces.
                try:
                    trace_doc = LocalJsonRepository(local_path).get_trace(trace_id)
                    source_name = "local"
                except (TraceNotFoundError, DataSourceError, TraceValidationError):
                    trace_doc = fetch_trace_by_id(
                        es_url=es_url,
                        index=index,
                        trace_id=trace_id,
                        username=username,
                        password=password,
                        verify_certs=verify_certs,
                        timeout_seconds=timeout_seconds,
                    )
                    source_name = "es"

            diagnosis = analyze_trace(trace_doc, top_n=3).to_dict()
            data = {
                "trace_id": trace_id,
                "tree": _span_to_dict(trace_doc.root),
                "diagnosis": diagnosis,
            }
            return jsonify(_envelope(data, source=source_name, degraded=False, start_ms=start))
        except TraceNotFoundError as exc:
            return jsonify(_envelope({}, source=source, degraded=True, start_ms=start, error=str(exc))), 404
        except TraceValidationError as exc:
            return jsonify(_envelope({}, source=source, degraded=True, start_ms=start, error=str(exc))), 422
        except DataSourceError as exc:
            return jsonify(_envelope({}, source=source, degraded=True, start_ms=start, error=str(exc))), 503
        except Exception as exc:
            return jsonify(_envelope({}, source=source, degraded=True, start_ms=start, error=str(exc))), 500

    @app.get("/api/logs/search")
    def api_logs_search() -> Any:
        start = time.time() * 1000
        trace_id = request.args.get("trace_id", "").strip()
        span_id = request.args.get("span_id", "").strip()
        keyword = request.args.get("keyword", "").strip()
        limit = max(1, min(200, int(request.args.get("limit", "50"))))

        es_url = request.args.get("es_url", "http://localhost:9200")
        index = request.args.get("index", "nebula_metrics")
        username = request.args.get("username")
        password = request.args.get("password")
        verify_certs = request.args.get("verify_certs", "true").lower() == "true"
        timeout_seconds = int(request.args.get("timeout_seconds", "10"))
        last_minutes = int(request.args.get("last_minutes", "30"))

        if not trace_id:
            return jsonify(_envelope({}, source="es", degraded=True, start_ms=start, error="trace_id_required")), 400

        try:
            trace_doc = fetch_trace_by_id(
                es_url=es_url,
                index=index,
                trace_id=trace_id,
                username=username,
                password=password,
                verify_certs=verify_certs,
                timeout_seconds=timeout_seconds,
            )
            target_span = _find_span(trace_doc.root, span_id) if span_id else None
            target_service = (
                target_span.service_name
                if target_span is not None
                else analyze_trace(trace_doc, top_n=1).bottleneck.span.service_name
            )

            logs_payload = search_service_logs(
                es_url=es_url,
                index=index,
                service_name=target_service,
                keyword=keyword,
                last_minutes=last_minutes,
                limit=limit,
                username=username,
                password=password,
                verify_certs=verify_certs,
                timeout_seconds=timeout_seconds,
            )

            data = {
                "query": {
                    "trace_id": trace_id,
                    "span_id": span_id or None,
                    "service_name": target_service,
                    "keyword": keyword,
                    "last_minutes": last_minutes,
                },
                "result": logs_payload,
                "paging": {
                    "cursor": None,
                    "next_cursor": None,
                    "mode": "offset_compat",
                },
            }
            return jsonify(_envelope(data, source="es", degraded=False, start_ms=start))
        except ESQueryError as exc:
            return jsonify(_envelope({}, source="es", degraded=True, start_ms=start, error=str(exc))), 502
        except Exception as exc:
            return jsonify(_envelope({}, source="es", degraded=True, start_ms=start, error=str(exc))), 500

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Nebula observability web dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
