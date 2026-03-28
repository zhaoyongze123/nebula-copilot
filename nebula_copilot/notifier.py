from __future__ import annotations

import json
from urllib import request


class NotifyError(RuntimeError):
    pass


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
