from __future__ import annotations

import json
import re
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


def _split_summary_sections(summary_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "header": [],
        "事件概览": [],
        "诊断结论": [],
        "关键证据": [],
        "建议动作": [],
    }
    current = "header"
    for raw in (summary_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        normalized = line.strip("[]")
        if normalized in sections and normalized != "header":
            current = normalized
            continue
        sections[current].append(line)
    return sections


def _detect_alert_template(summary_text: str) -> str:
    text = summary_text or ""
    if "[P1]" in text:
        return "red"
    if "[P2]" in text:
        return "orange"
    return "blue"


def _trim_line_prefix(text: str) -> str:
    return re.sub(r"^[-*]\s*", "", text or "").strip()


def _as_lark_bullets(lines: list[str]) -> str:
    cleaned = [_trim_line_prefix(item) for item in lines if _trim_line_prefix(item)]
    if not cleaned:
        return "- 无"
    return "\n".join(f"- {item}" for item in cleaned)


def _extract_compact_title(summary_text: str) -> str:
    text = " ".join((summary_text or "").split())
    match = re.search(r"(【Nebula[^】]*】\s*\[P[123]\]\s*[^，。；!！?\n]{1,48})", text)
    if match:
        return match.group(1).strip()

    for raw in (summary_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(line) > 56:
            return f"{line[:56].rstrip()}..."
        return line

    return "Nebula 告警"


def _extract_sections_from_unstructured(summary_text: str) -> dict[str, list[str]]:
    text = (summary_text or "").strip()
    result: dict[str, list[str]] = {
        "事件概览": [],
        "诊断结论": [],
        "关键证据": [],
        "建议动作": [],
    }

    if not text:
        return result

    trace_id = re.search(r"Trace(?:\s*ID)?\s*[:：]\s*([A-Za-z0-9_-]+)", text, flags=re.I)
    if trace_id:
        result["事件概览"].append(f"Trace: {trace_id.group(1)}")

    run_id = re.search(r"Run(?:\s*ID)?\s*[:：]\s*([A-Za-z0-9_-]+)", text, flags=re.I)
    if run_id:
        result["事件概览"].append(f"Run: {run_id.group(1)}")

    service = re.search(r"(?:瓶颈服务|涉及)\s*[:：]?\s*([A-Za-z0-9_-]+(?:-service)?)", text)
    if service:
        result["事件概览"].append(f"服务: {service.group(1)}")

    operation = re.search(r"操作(?:为)?\s*[:：]?\s*([^，。\n]+)", text)
    if operation:
        result["事件概览"].append(f"操作: {operation.group(1).strip()}")

    duration = re.search(r"耗时\s*[:：]?\s*([0-9]+ms)", text)
    if duration:
        result["事件概览"].append(f"耗时: {duration.group(1)}")

    for key in ["异常类型", "模式比对", "关联查询", "LLM根因", "LLM置信度", "诊断"]:
        m = re.search(rf"{key}\s*[:：]\s*([^\n]+)", text)
        if m:
            result["诊断结论"].append(f"{key}: {m.group(1).strip()}")

    if not result["诊断结论"]:
        sentences = [seg.strip() for seg in re.split(r"[。；]\s*", text) if seg.strip()]
        for seg in sentences:
            if any(token in seg for token in ["诊断", "异常", "根因", "数据库", "下游", "超时"]):
                result["诊断结论"].append(seg)
                break

    for key in ["JVM证据", "日志证据", "链路排查建议", "异常摘要"]:
        m = re.search(rf"{key}\s*[:：]\s*([^\n]+)", text)
        if m:
            result["关键证据"].append(f"{key}: {m.group(1).strip()}")

    if not result["关键证据"]:
        evidence_hits = []
        for seg in re.split(r"[。；]\s*", text):
            seg = seg.strip()
            if not seg:
                continue
            if any(token in seg.lower() for token in ["jvm", "p95", "heap", "gc", "日志", "error_rate", "证据"]):
                evidence_hits.append(seg)
        result["关键证据"] = evidence_hits[:2]

    action = re.search(r"建议动作\s*[:：]\s*([^\n]+)", text)
    if action:
        result["建议动作"].append(f"建议动作: {action.group(1).strip()}")
    else:
        for seg in re.split(r"[。；]\s*", text):
            seg = seg.strip()
            if "建议" in seg:
                result["建议动作"].append(seg)
                break

    return result


def _build_feishu_card_payload(summary_text: str) -> dict:
    sections = _split_summary_sections(summary_text)
    fallback_sections = _extract_sections_from_unstructured(summary_text)
    for section_name in ["事件概览", "诊断结论", "关键证据", "建议动作"]:
        if not sections[section_name]:
            sections[section_name] = fallback_sections[section_name]

    title = _extract_compact_title(summary_text)
    event_md = _as_lark_bullets(sections["事件概览"])
    diagnose_md = _as_lark_bullets(sections["诊断结论"])
    evidence_lines = sections["关键证据"]
    action_lines = sections["建议动作"]
    action_md = _as_lark_bullets(action_lines)
    evidence_preview = _trim_line_prefix(evidence_lines[0]) if evidence_lines else "无"
    evidence_md = _as_lark_bullets(evidence_lines)

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
                "enable_forward": True,
            },
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": _detect_alert_template(summary_text),
            },
            "body": {
                "direction": "vertical",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "**关键信息速览**\n"
                        "<font color='red'>优先处理 [建议动作] 中第一条，先止损再定位。</font>",
                    },
                    {
                        "tag": "column_set",
                        "horizontal_spacing": "12px",
                        "columns": [
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [
                                    {
                                        "tag": "markdown",
                                        "content": "**事件概览**\n" + event_md,
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [
                                    {
                                        "tag": "markdown",
                                        "content": "**诊断结论**\n" + diagnose_md,
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "tag": "column_set",
                        "horizontal_spacing": "12px",
                        "columns": [
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [
                                    {
                                        "tag": "markdown",
                                        "content": "**建议动作**\n" + action_md,
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [
                                    {
                                        "tag": "markdown",
                                        "content": "**证据预览（折叠）**\n" + f"- {evidence_preview}",
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "tag": "collapsible_panel",
                        "expanded": False,
                        "header": {
                            "title": {"tag": "plain_text", "content": "展开查看完整证据"}
                        },
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": evidence_md,
                            }
                        ],
                    },
                ],
            },
        },
    }


def _build_plain_text_payload(summary_text: str) -> dict:
    return {"msg_type": "text", "content": {"text": summary_text}}


def _webhook_response_error(resp_body: str) -> str | None:
    if not resp_body:
        return None
    try:
        parsed = json.loads(resp_body)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    code = parsed.get("code")
    if isinstance(code, int) and code != 0:
        msg = str(parsed.get("msg") or parsed.get("message") or "unknown error")
        return f"Webhook business error code={code}, message={msg}"

    status_code = parsed.get("StatusCode")
    if isinstance(status_code, int) and status_code != 0:
        msg = str(parsed.get("StatusMessage") or parsed.get("msg") or "unknown error")
        return f"Webhook business error status={status_code}, message={msg}"

    return None


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

    Prefer Feishu interactive card to improve readability and actionability.
    If card payload is rejected by webhook, fallback to plain text once.
    """

    def _post(payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read().decode("utf-8", errors="ignore")
            if status < 200 or status >= 300:
                raise NotifyError(f"Webhook responded with status={status}")
            biz_error = _webhook_response_error(body)
            if biz_error:
                raise NotifyError(biz_error)

    try:
        _post(_build_feishu_card_payload(summary_text))
    except Exception:  # pragma: no cover
        try:
            _post(_build_plain_text_payload(summary_text))
        except Exception as text_exc:  # pragma: no cover
            raise NotifyError(str(text_exc)) from text_exc


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
