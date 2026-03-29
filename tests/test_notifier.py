from pathlib import Path

from nebula_copilot.notifier import NotifyError, push_summary_reliable


def test_push_summary_reliable_dedup(monkeypatch, tmp_path: Path) -> None:
    calls = {"count": 0}

    def _ok_push(webhook_url: str, summary_text: str, timeout_seconds: int = 8) -> None:
        calls["count"] += 1

    monkeypatch.setattr("nebula_copilot.notifier.push_summary", _ok_push)

    cache_path = tmp_path / "notify_dedupe.json"
    first = push_summary_reliable(
        "https://example.com/webhook",
        "summary",
        "trace-1:webhook",
        dedupe_cache_path=cache_path,
        dedupe_window_seconds=300,
        max_retries=3,
        backoff_seconds=0,
    )
    second = push_summary_reliable(
        "https://example.com/webhook",
        "summary",
        "trace-1:webhook",
        dedupe_cache_path=cache_path,
        dedupe_window_seconds=300,
        max_retries=3,
        backoff_seconds=0,
    )

    assert first.status == "ok"
    assert second.status == "skipped"
    assert second.deduplicated is True
    assert calls["count"] == 1


def test_push_summary_reliable_retry_success(monkeypatch, tmp_path: Path) -> None:
    calls = {"count": 0}

    def _flaky_push(webhook_url: str, summary_text: str, timeout_seconds: int = 8) -> None:
        calls["count"] += 1
        if calls["count"] < 2:
            raise NotifyError("network error")

    monkeypatch.setattr("nebula_copilot.notifier.push_summary", _flaky_push)

    result = push_summary_reliable(
        "https://example.com/webhook",
        "summary",
        "trace-2:webhook",
        dedupe_cache_path=tmp_path / "notify_dedupe.json",
        dedupe_window_seconds=60,
        max_retries=3,
        backoff_seconds=0,
    )

    assert result.status == "ok"
    assert result.attempts == 2


def test_push_summary_reliable_retry_exhausted(monkeypatch, tmp_path: Path) -> None:
    def _fail_push(webhook_url: str, summary_text: str, timeout_seconds: int = 8) -> None:
        raise NotifyError("always fail")

    monkeypatch.setattr("nebula_copilot.notifier.push_summary", _fail_push)

    result = push_summary_reliable(
        "https://example.com/webhook",
        "summary",
        "trace-3:webhook",
        dedupe_cache_path=tmp_path / "notify_dedupe.json",
        dedupe_window_seconds=60,
        max_retries=3,
        backoff_seconds=0,
    )

    assert result.status == "failed"
    assert result.attempts == 3
    assert "always fail" in (result.error or "")
