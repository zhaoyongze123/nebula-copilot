from __future__ import annotations

import json
from pathlib import Path

from nebula_copilot.mock_data import DEFAULT_TRACE_ID, build_mock_trace, write_mock_file
from nebula_copilot.web import create_app


def _make_runs(path: Path) -> None:
    payload = [
        {
            "run_id": "run-1",
            "trace_id": DEFAULT_TRACE_ID,
            "status": "failed",
            "started_at": "2026-01-01T10:00:00+00:00",
            "finished_at": "2026-01-01T10:00:01+00:00",
            "history": [{"timestamp": "2026-01-01T10:00:00+00:00", "phase": "analyze", "message": "start"}],
            "diagnosis": {"summary": "demo diagnosis"},
            "metrics": {"duration_ms": 1001},
            "notify": {"status": "ok", "attempts": 1},
        },
        {
            "run_id": "run-2",
            "trace_id": "trace-2",
            "status": "ok",
            "started_at": "2026-01-01T09:00:00+00:00",
            "finished_at": "2026-01-01T09:00:01+00:00",
            "history": [],
            "diagnosis": {"summary": "ok"},
            "metrics": {"duration_ms": 200},
            "notify": {"status": "ok", "attempts": 1},
        },
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_dashboard_route() -> None:
    app = create_app()
    client = app.test_client()

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Nebula" in resp.get_data(as_text=True)


def test_overview_and_runs_api(tmp_path: Path) -> None:
    app = create_app()
    client = app.test_client()

    runs_path = tmp_path / "runs.json"
    _make_runs(runs_path)

    overview = client.get(f"/api/overview?runs_path={runs_path}")
    assert overview.status_code == 200
    body = overview.get_json()
    assert body["ok"] is True
    assert body["data"]["kpi"]["total"] == 2

    runs = client.get(f"/api/runs?runs_path={runs_path}&sort=error_first&size=10")
    assert runs.status_code == 200
    runs_body = runs.get_json()
    items = runs_body["data"]["items"]
    assert len(items) == 2
    assert items[0]["run_id"] == "run-1"


def test_run_detail_api(tmp_path: Path) -> None:
    app = create_app()
    client = app.test_client()

    runs_path = tmp_path / "runs.json"
    _make_runs(runs_path)

    detail = client.get(f"/api/runs/run-1/page?runs_path={runs_path}")
    assert detail.status_code == 200
    body = detail.get_json()
    assert body["data"]["summary"]["run_id"] == "run-1"
    assert body["data"]["diagnosis"]["summary"] == "demo diagnosis"


def test_trace_inspect_local(tmp_path: Path) -> None:
    app = create_app()
    client = app.test_client()

    trace_path = tmp_path / "mock_trace.json"
    write_mock_file(trace_path, trace_id=DEFAULT_TRACE_ID, scenario="timeout")

    resp = client.get(f"/api/traces/{DEFAULT_TRACE_ID}/inspect?source=local&local_path={trace_path}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["trace_id"] == DEFAULT_TRACE_ID
    assert body["data"]["tree"]["service_name"]


def test_trace_inspect_local_not_found(tmp_path: Path) -> None:
    app = create_app()
    client = app.test_client()

    trace_path = tmp_path / "mock_trace.json"
    write_mock_file(trace_path, trace_id=DEFAULT_TRACE_ID, scenario="timeout")

    resp = client.get(f"/api/traces/not-exists/inspect?source=local&local_path={trace_path}")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["ok"] is False


def test_trace_inspect_auto_fallback_to_es(monkeypatch, tmp_path: Path) -> None:
    app = create_app()
    client = app.test_client()

    trace_path = tmp_path / "mock_trace.json"
    write_mock_file(trace_path, trace_id=DEFAULT_TRACE_ID, scenario="timeout")
    trace_doc = build_mock_trace("real-es-trace", "timeout")

    monkeypatch.setattr("nebula_copilot.web.app.fetch_trace_by_id", lambda **_: trace_doc)

    resp = client.get(f"/api/traces/real-es-trace/inspect?local_path={trace_path}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["meta"]["source"] == "es"


def test_trace_inspect_es_explicit(monkeypatch) -> None:
    app = create_app()
    client = app.test_client()
    trace_doc = build_mock_trace("trace-es-explicit", "timeout")

    monkeypatch.setattr("nebula_copilot.web.app.fetch_trace_by_id", lambda **_: trace_doc)

    resp = client.get("/api/traces/trace-es-explicit/inspect?source=es")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["meta"]["source"] == "es"


def test_logs_search_api_with_monkeypatch(monkeypatch) -> None:
    app = create_app()
    client = app.test_client()

    trace_doc = build_mock_trace(DEFAULT_TRACE_ID, "timeout")

    monkeypatch.setattr("nebula_copilot.web.app.fetch_trace_by_id", lambda **_: trace_doc)
    monkeypatch.setattr(
        "nebula_copilot.web.app.search_service_logs",
        lambda **_: {"service_name": "inventory-service", "logs": [{"message": "timeout"}], "total": 1},
    )

    resp = client.get(f"/api/logs/search?trace_id={DEFAULT_TRACE_ID}&keyword=timeout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["result"]["total"] == 1
