from __future__ import annotations

import argparse
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request

from nebula_copilot.analyzer import analyze_trace
from nebula_copilot.cli import _load_run_records
from nebula_copilot.config import load_app_config
from nebula_copilot.errors import DataSourceError, TraceNotFoundError, TraceValidationError
from nebula_copilot.es_client import ESQueryError, fetch_trace_by_id, search_service_logs
from nebula_copilot.es_importer import ESImporter, ImportError
from nebula_copilot.es_sync import ESSync, SyncError
from nebula_copilot.knowledge_base import KnowledgeBase
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
    env_file = Path(os.getenv("NEBULA_ENV_FILE", ".env"))
    app_config = load_app_config(env_file)
    knowledge_base = KnowledgeBase.from_app_config(app_config)

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    # 全局导入/同步状态存储
    # 格式：{task_id: {"status": "running|done|error", "progress": 0-100, "error": "...", "created_at": "...", "updated_at": "..."}}
    import_tasks: dict[str, dict[str, Any]] = {}
    es_sync_instance: ESSync | None = None

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

            diagnosis = analyze_trace(trace_doc, top_n=3, knowledge_base=knowledge_base).to_dict()
            data = {
                "trace_id": trace_id,
                "tree": _span_to_dict(trace_doc.root),
                "diagnosis": diagnosis,
            }
            return jsonify(_envelope(data, source=source_name, degraded=False, start_ms=start))
        except TraceNotFoundError as exc:
            return jsonify(_envelope({}, source=source, degraded=True, start_ms=start, error=str(exc))), 404
        except ESQueryError as exc:
            # ES 中找不到 trace，当作 404 处理
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
        service_name = request.args.get("service_name", "").strip()  # 新增：直接指定 service
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
            
            # 确定目标 service：优先使用直接指定的 service_name，其次查找 span，最后用瓶颈 service
            target_service = service_name
            if not target_service:
                target_span = _find_span(trace_doc.root, span_id) if span_id else None
                target_service = (
                    target_span.service_name
                    if target_span is not None
                    else analyze_trace(trace_doc, top_n=1, knowledge_base=knowledge_base).bottleneck.span.service_name
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
            # trace 不存在，返回 404
            return jsonify(_envelope({}, source="es", degraded=True, start_ms=start, error=str(exc))), 404
        except Exception as exc:
            return jsonify(_envelope({}, source="es", degraded=True, start_ms=start, error=str(exc))), 500

    @app.post("/api/import/start")
    def api_import_start() -> Any:
        """启动 ES 批量导入。

        查询参数：
        - from_date: 开始时间（ISO 8601，例如 2025-03-20T00:00:00）
        - to_date: 结束时间（ISO 8601）
        - limit: 导入数量上限（默认 1000）
        - es_url: Elasticsearch 地址（默认 localhost:9200）
        - index: 索引名（默认 nebula_metrics）
        - username: ES 用户名
        - password: ES 密码
        - output_path: 输出文件路径（默认 data/agent_runs.json）

        返回：{task_id, status, created_at}
        """
        start = time.time() * 1000

        try:
            from_date_str = request.args.get("from_date")
            to_date_str = request.args.get("to_date")
            if not from_date_str or not to_date_str:
                return jsonify(
                    _envelope({}, source="es", degraded=True, start_ms=start, error="from_date and to_date required")
                ), 400

            from_date = datetime.fromisoformat(from_date_str)
            to_date = datetime.fromisoformat(to_date_str)
            limit = int(request.args.get("limit", "1000"))

            es_url = request.args.get("es_url") or os.getenv("NEBULA_ES_URL", "http://localhost:9200")
            index = request.args.get("index") or os.getenv("NEBULA_ES_INDEX", "nebula_metrics")
            username = request.args.get("username") or os.getenv("NEBULA_ES_USERNAME")
            password = request.args.get("password") or os.getenv("NEBULA_ES_PASSWORD")
            output_path = Path(request.args.get("output_path", "data/agent_runs.json"))

            task_id = str(uuid.uuid4())[:8]
            import_tasks[task_id] = {
                "status": "running",
                "progress": 0,
                "error": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }

            # 在新线程中执行导入
            def _do_import() -> None:
                try:
                    importer = ESImporter(
                        es_url=es_url,
                        index=index,
                        username=username,
                        password=password,
                    )
                    runs = importer.import_traces(from_date=from_date, to_date=to_date, limit=limit)
                    importer.save_runs(runs, output_path)

                    import_tasks[task_id]["status"] = "done"
                    import_tasks[task_id]["progress"] = 100
                    import_tasks[task_id]["result"] = {"imported_count": len(runs)}
                except ImportError as exc:
                    import_tasks[task_id]["status"] = "error"
                    import_tasks[task_id]["error"] = str(exc)
                finally:
                    import_tasks[task_id]["updated_at"] = datetime.now().isoformat()

            import threading

            thread = threading.Thread(target=_do_import, daemon=True)
            thread.start()

            data = {
                "task_id": task_id,
                "status": "running",
                "created_at": import_tasks[task_id]["created_at"],
            }
            return jsonify(_envelope(data, source="local", degraded=False, start_ms=start))

        except ValueError as exc:
            return jsonify(
                _envelope({}, source="es", degraded=True, start_ms=start, error=f"Invalid date format: {exc}")
            ), 400
        except Exception as exc:
            return jsonify(_envelope({}, source="es", degraded=True, start_ms=start, error=str(exc))), 500

    @app.get("/api/import/<task_id>/status")
    def api_import_status(task_id: str) -> Any:
        """查询导入进度。

        返回：{task_id, status, progress, error, created_at, updated_at, result}
        """
        start = time.time() * 1000

        if task_id not in import_tasks:
            return jsonify(_envelope({}, source="local", degraded=True, start_ms=start, error="task_not_found")), 404

        task = import_tasks[task_id]
        data = {
            "task_id": task_id,
            "status": task.get("status"),
            "progress": task.get("progress", 0),
            "error": task.get("error"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "result": task.get("result"),
        }
        return jsonify(_envelope(data, source="local", degraded=False, start_ms=start))

    @app.get("/api/sync/status")
    def api_sync_status() -> Any:
        """查询自动同步状态。

        返回：{is_running, last_sync_time, total_synced, total_errors}
        """
        start = time.time() * 1000
        nonlocal es_sync_instance

        if es_sync_instance is None:
            data = {
                "is_running": False,
                "last_sync_time": None,
                "total_synced": 0,
                "total_errors": 0,
            }
        else:
            data = es_sync_instance.get_sync_status()

        return jsonify(_envelope(data, source="local", degraded=False, start_ms=start))

    @app.post("/api/sync/start")
    def api_sync_start() -> Any:
        """启动自动同步。

        查询参数：
        - interval_seconds: 同步间隔（默认 300）
        - lookback_minutes: 回溯窗口（默认 60）
        - es_url: Elasticsearch 地址（默认 localhost:9200）
        - index: 索引名（默认 nebula_metrics）
        - username: ES 用户名
        - password: ES 密码
        - output_path: 输出文件路径（默认 data/agent_runs.json）

        返回：{status: "started"}
        """
        start = time.time() * 1000
        nonlocal es_sync_instance

        try:
            es_url = request.args.get("es_url") or os.getenv("NEBULA_ES_URL", "http://localhost:9200")
            index = request.args.get("index") or os.getenv("NEBULA_ES_INDEX", "nebula_metrics")
            username = request.args.get("username") or os.getenv("NEBULA_ES_USERNAME")
            password = request.args.get("password") or os.getenv("NEBULA_ES_PASSWORD")
            output_path = Path(request.args.get("output_path", "data/agent_runs.json"))
            interval_seconds = int(request.args.get("interval_seconds", "300"))
            lookback_minutes = int(request.args.get("lookback_minutes", "60"))

            if es_sync_instance is None:
                es_sync_instance = ESSync(
                    es_url=es_url,
                    index=index,
                    output_path=output_path,
                    username=username,
                    password=password,
                )

            es_sync_instance.start_periodic_sync(interval_seconds=interval_seconds, lookback_minutes=lookback_minutes)

            data = {"status": "started"}
            return jsonify(_envelope(data, source="local", degraded=False, start_ms=start))

        except SyncError as exc:
            return jsonify(_envelope({}, source="local", degraded=True, start_ms=start, error=str(exc))), 409
        except Exception as exc:
            return jsonify(_envelope({}, source="local", degraded=True, start_ms=start, error=str(exc))), 500

    @app.post("/api/sync/stop")
    def api_sync_stop() -> Any:
        """停止自动同步。

        返回：{status: "stopped"}
        """
        start = time.time() * 1000
        nonlocal es_sync_instance

        try:
            if es_sync_instance is not None:
                es_sync_instance.stop_sync()

            data = {"status": "stopped"}
            return jsonify(_envelope(data, source="local", degraded=False, start_ms=start))

        except Exception as exc:
            return jsonify(_envelope({}, source="local", degraded=True, start_ms=start, error=str(exc))), 500

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
