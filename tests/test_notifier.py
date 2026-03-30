from pathlib import Path

from nebula_copilot.notifier import (
    NotifyError,
    _build_feishu_card_payload,
    _webhook_response_error,
    push_summary,
    push_summary_reliable,
)


class _DummyResponse:
    def __init__(self, status: int = 200, body: str = "") -> None:
        self.status = status
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


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


def test_build_feishu_card_payload_contains_columns_and_fold() -> None:
    summary = """【Nebula 告警】[P1] 下游超时
[事件概览]
Trace: trace-1
Run: run-1

[诊断结论]
异常类型: Timeout
模式比对: 依赖挂掉

[关键证据]
JVM证据: p95=1880ms
日志证据: 命中12条

[建议动作]
建议动作: 先扩容后排查"""

    payload = _build_feishu_card_payload(summary)
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["schema"] == "2.0"
    elements = payload["card"]["body"]["elements"]
    assert any(item.get("tag") == "column_set" for item in elements)
    assert any(item.get("tag") == "collapsible_panel" for item in elements)


def test_webhook_response_error_parse() -> None:
    assert _webhook_response_error('{"code":0,"msg":"ok"}') is None
    err = _webhook_response_error('{"code":19002,"msg":"invalid card"}')
    assert err is not None
    assert "19002" in err


def test_push_summary_fallback_to_text_when_card_rejected(monkeypatch) -> None:
    calls = {"count": 0}

    def _fake_urlopen(req, timeout=8):
        calls["count"] += 1
        if calls["count"] == 1:
            return _DummyResponse(200, '{"code":19002,"msg":"invalid card"}')
        return _DummyResponse(200, '{"code":0,"msg":"ok"}')

    monkeypatch.setattr("nebula_copilot.notifier.request.urlopen", _fake_urlopen)

    push_summary("https://example.com/webhook", "summary")
    assert calls["count"] == 2


def test_build_feishu_card_payload_for_unstructured_summary() -> None:
    summary = (
        "【Nebula 告警】 [P1] 数据库异常。事件涉及 pricing-service 的 RPC calculatePromotion 操作，"
        "耗时169ms。诊断显示数据库相关错误，可能由慢SQL或锁等待（如死锁）引起。"
        "JVM指标正常且错误率较低。建议重点检查 pricing-service 的慢SQL和锁等待情况。"
    )

    payload = _build_feishu_card_payload(summary)
    header_title = payload["card"]["header"]["title"]["content"]
    assert len(header_title) <= 60

    body_elements = payload["card"]["body"]["elements"]
    first_column_set = next(item for item in body_elements if item.get("tag") == "column_set")
    left_content = first_column_set["columns"][0]["elements"][0]["content"]
    right_content = first_column_set["columns"][1]["elements"][0]["content"]

    assert "- 无" not in left_content
    assert "- 无" not in right_content
