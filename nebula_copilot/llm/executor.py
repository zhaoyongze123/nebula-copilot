from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LLMSettings:
    enabled: bool
    provider: str
    model: str
    api_key: Optional[str]
    base_url: str
    timeout_ms: int
    max_retry: int
    report_polish_enabled: bool


class LLMExecutor:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    @classmethod
    def disabled(cls) -> "LLMExecutor":
        return cls(
            LLMSettings(
                enabled=False,
                provider="github",
                model="",
                api_key=None,
                base_url="",
                timeout_ms=0,
                max_retry=0,
                report_polish_enabled=False,
            )
        )

    def can_use(self) -> bool:
        return bool(self.settings.enabled and self.settings.api_key)

    def _run_chain(self, system_prompt: str, user_prompt: str) -> str:
        if not self.can_use():
            raise RuntimeError("LLM not enabled or api key missing")

        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_openai import ChatOpenAI
        except Exception as exc:
            raise RuntimeError("LangChain dependencies are not installed") from exc

        model = ChatOpenAI(
            model=self.settings.model,
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_ms / 1000,
            max_retries=self.settings.max_retry,
            temperature=0,
        )

        resp = model.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        content = getattr(resp, "content", "")
        if isinstance(content, str):
            return content
        return str(content)

    def suggest_action(self, error_type: str, service_name: str, exception_stack: str | None) -> Optional[str]:
        if not self.can_use():
            return None

        system_prompt = (
            "你是资深SRE。基于给定异常类型和异常栈，输出可执行排障建议。"
            "必须只输出JSON，格式: {\"action\": \"...\"}。"
        )
        user_prompt = (
            f"error_type={error_type}\n"
            f"service_name={service_name}\n"
            f"exception_stack={(exception_stack or '')[:600]}"
        )

        raw = self._run_chain(system_prompt, user_prompt)
        try:
            payload = json.loads(raw)
            action = str(payload.get("action", "")).strip()
            return action or None
        except Exception:
            return None

    def polish_summary(self, summary: str) -> Optional[str]:
        if not self.can_use() or not self.settings.report_polish_enabled:
            return None

        system_prompt = (
            "你是值班排障助手。请在不改变事实的前提下润色摘要。"
            "必须只输出JSON，格式: {\"summary\": \"...\"}。"
        )
        user_prompt = f"原始摘要:\n{summary[:1200]}"

        raw = self._run_chain(system_prompt, user_prompt)
        try:
            payload = json.loads(raw)
            polished = str(payload.get("summary", "")).strip()
            return polished or None
        except Exception:
            return None

    def diagnose_incident(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Use LLM to produce structured incident decision.

        Returns a dict with optional keys:
        - problem_type
        - root_cause
        - action
        - confidence (0~1)
        - linkage_suspected (bool)
        - linkage_action
        """
        if not self.can_use():
            return None

        system_prompt = (
            "你是生产环境值班SRE，基于链路/JVM/日志证据做根因判断。"
            "必须只输出JSON，格式: "
            "{\"problem_type\":\"...\",\"root_cause\":\"...\",\"action\":\"...\",\"confidence\":0.0,\"linkage_suspected\":false,\"linkage_action\":\"...\"}" 
            "problem_type取值建议: Timeout/DB/Downstream/Unknown/None。"
            "当怀疑存在链路背压、上下游级联故障或跨服务传播时，linkage_suspected=true，并给出linkage_action。"
            "不要输出任何额外文本。"
        )
        user_prompt = f"incident_context={json.dumps(context, ensure_ascii=False)[:5000]}"

        raw = self._run_chain(system_prompt, user_prompt)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return None

            decision: Dict[str, Any] = {}
            problem_type = str(payload.get("problem_type", "")).strip()
            root_cause = str(payload.get("root_cause", "")).strip()
            action = str(payload.get("action", "")).strip()
            confidence_val = payload.get("confidence")
            linkage_suspected = payload.get("linkage_suspected")
            linkage_action = str(payload.get("linkage_action", "")).strip()

            if problem_type:
                decision["problem_type"] = problem_type
            if root_cause:
                decision["root_cause"] = root_cause
            if action:
                decision["action"] = action
            try:
                if confidence_val is not None:
                    c = float(confidence_val)
                    decision["confidence"] = max(0.0, min(1.0, c))
            except (TypeError, ValueError):
                pass

            if isinstance(linkage_suspected, bool):
                decision["linkage_suspected"] = linkage_suspected
            if linkage_action:
                decision["linkage_action"] = linkage_action

            return decision or None
        except Exception:
            return None
