from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


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
            from langchain_core.output_parsers import StrOutputParser
            from langchain_core.prompts import ChatPromptTemplate
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

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("human", user_prompt),
            ]
        )
        chain = prompt | model | StrOutputParser()
        return chain.invoke({})

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
