from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict


def _load_guard_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"dedupe": {}, "rate": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"dedupe": {}, "rate": {}}
        dedupe = data.get("dedupe") if isinstance(data.get("dedupe"), dict) else {}
        rate = data.get("rate") if isinstance(data.get("rate"), dict) else {}
        return {"dedupe": dedupe, "rate": rate}
    except (OSError, json.JSONDecodeError):
        return {"dedupe": {}, "rate": {}}


def _save_guard_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _within_window(last_ts: str, now: datetime, window_seconds: int) -> bool:
    try:
        dt = datetime.fromisoformat(last_ts)
    except ValueError:
        return False
    return now - dt <= timedelta(seconds=window_seconds)


def evaluate_run_guard(
    *,
    path: Path,
    trace_id: str,
    run_id: str,
    dedupe_window_seconds: int,
    rate_limit_per_minute: int,
    now_fn: Callable[[], datetime] = datetime.now,
) -> Dict[str, Any]:
    now = now_fn()
    state = _load_guard_state(path)

    dedupe_state = state["dedupe"]
    rate_state = state["rate"]

    last = dedupe_state.get(trace_id)
    if isinstance(last, dict):
        last_ts = str(last.get("ts") or "")
        if _within_window(last_ts, now, dedupe_window_seconds):
            return {
                "allowed": False,
                "status": "deduped",
                "reason": "trace dedup window hit",
                "previous_run_id": str(last.get("run_id") or ""),
            }

    minute_bucket = now.strftime("%Y%m%d%H%M")
    key = f"bucket:{minute_bucket}"
    bucket_count = int(rate_state.get(key, 0))
    if rate_limit_per_minute > 0 and bucket_count >= rate_limit_per_minute:
        return {
            "allowed": False,
            "status": "rate_limited",
            "reason": "rate limit exceeded",
            "previous_run_id": None,
        }

    dedupe_state[trace_id] = {"run_id": run_id, "ts": now.isoformat(timespec="seconds")}
    rate_state[key] = bucket_count + 1

    # Keep recent minute buckets only.
    recent_prefix = now.strftime("%Y%m%d%H")
    state["rate"] = {k: v for k, v in rate_state.items() if str(k).startswith(f"bucket:{recent_prefix}")}
    _save_guard_state(path, state)

    return {
        "allowed": True,
        "status": "allowed",
        "reason": "ok",
        "previous_run_id": None,
    }
