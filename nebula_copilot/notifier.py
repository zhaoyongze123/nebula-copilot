from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from urllib import request


class NotifyError(RuntimeError):
    pass


@dataclass
class NotifyResult:
    status: str
    deduplicated: bool
    attempts: int
    error: str | None = None


def _load_dedupe_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_dedupe_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_within_window(last_sent_at: str, window_seconds: int) -> bool:
    try:
        sent_at = datetime.fromisoformat(last_sent_at)
    except ValueError:
        return False
    return datetime.now() - sent_at <= timedelta(seconds=window_seconds)


def push_summary(webhook_url: str, summary_text: str, timeout_seconds: int = 8) -> None:
    """Push summary to Feishu/DingTalk style webhook.

    Payload uses a generic text message body compatible with most chat webhooks.
    """
    payload = {"msg_type": "text", "content": {"text": summary_text}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            status = getattr(resp, "status", 200)
            if status < 200 or status >= 300:
                raise NotifyError(f"Webhook responded with status={status}")
    except Exception as exc:  # pragma: no cover
        raise NotifyError(str(exc)) from exc


def push_summary_reliable(
    webhook_url: str,
    summary_text: str,
    dedupe_key: str,
    *,
    dedupe_cache_path: Path = Path("data/notify_dedupe.json"),
    dedupe_window_seconds: int = 300,
    max_retries: int = 3,
    timeout_seconds: int = 8,
    backoff_seconds: float = 0.2,
) -> NotifyResult:
    cache = _load_dedupe_cache(dedupe_cache_path)
    cached_at = cache.get(dedupe_key)
    if cached_at and _is_within_window(cached_at, dedupe_window_seconds):
        return NotifyResult(status="skipped", deduplicated=True, attempts=0)

    attempts = 0
    last_error: str | None = None
    total_attempts = max(1, max_retries)

    for idx in range(total_attempts):
        attempts = idx + 1
        try:
            push_summary(webhook_url, summary_text, timeout_seconds=timeout_seconds)
            cache[dedupe_key] = datetime.now().isoformat(timespec="seconds")
            _save_dedupe_cache(dedupe_cache_path, cache)
            return NotifyResult(status="ok", deduplicated=False, attempts=attempts)
        except NotifyError as exc:
            last_error = str(exc)
            if attempts < total_attempts and backoff_seconds > 0:
                sleep(backoff_seconds * (2 ** idx))

    return NotifyResult(status="failed", deduplicated=False, attempts=attempts, error=last_error)
