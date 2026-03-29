from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict


def _json_size(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def _truncate_payload(payload: Dict[str, Any], max_bytes: int = 2048) -> tuple[Dict[str, Any], Dict[str, Any]]:
    raw = json.dumps(payload, ensure_ascii=False)
    original_size = len(raw)
    if original_size <= max_bytes:
        return payload, {
            "is_truncated": False,
            "original_size": original_size,
            "returned_size": original_size,
        }

    truncated_raw = raw[: max_bytes - 3] + "..."
    truncated_payload = {
        "_truncated_json": truncated_raw,
        "_note": "payload too large, returned compact preview",
    }
    returned_size = _json_size(truncated_payload)
    return truncated_payload, {
        "is_truncated": True,
        "original_size": original_size,
        "returned_size": returned_size,
    }


def build_tool_response(
    tool_name: str,
    target: str,
    payload: Dict[str, Any],
    status: str = "ok",
    summary: str | None = None,
) -> Dict[str, Any]:
    payload_compact, truncation = _truncate_payload(payload)
    return {
        "status": status,
        "tool": tool_name,
        "target": target,
        "payload": payload_compact,
        "error": None,
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "payload_size": _json_size(payload_compact),
        },
        "summary": summary or f"{tool_name} 执行完成，目标={target}",
        "truncation": truncation,
    }
