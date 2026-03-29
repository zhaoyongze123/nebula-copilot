from datetime import datetime
from pathlib import Path

from nebula_copilot.runtime_guard import evaluate_run_guard


def test_run_guard_dedup_hit(tmp_path: Path) -> None:
    path = tmp_path / "guard.json"
    now = datetime(2026, 3, 29, 12, 0, 0)

    first = evaluate_run_guard(
        path=path,
        trace_id="trace-1",
        run_id="run-1",
        dedupe_window_seconds=300,
        rate_limit_per_minute=10,
        now_fn=lambda: now,
    )
    second = evaluate_run_guard(
        path=path,
        trace_id="trace-1",
        run_id="run-2",
        dedupe_window_seconds=300,
        rate_limit_per_minute=10,
        now_fn=lambda: now,
    )

    assert first["allowed"] is True
    assert second["allowed"] is False
    assert second["status"] == "deduped"
    assert second["previous_run_id"] == "run-1"


def test_run_guard_rate_limit_hit(tmp_path: Path) -> None:
    path = tmp_path / "guard.json"
    now = datetime(2026, 3, 29, 12, 1, 0)

    first = evaluate_run_guard(
        path=path,
        trace_id="trace-a",
        run_id="run-a",
        dedupe_window_seconds=1,
        rate_limit_per_minute=1,
        now_fn=lambda: now,
    )
    second = evaluate_run_guard(
        path=path,
        trace_id="trace-b",
        run_id="run-b",
        dedupe_window_seconds=1,
        rate_limit_per_minute=1,
        now_fn=lambda: now,
    )

    assert first["allowed"] is True
    assert second["allowed"] is False
    assert second["status"] == "rate_limited"
