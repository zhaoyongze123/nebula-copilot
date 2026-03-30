from nebula_copilot.llm.executor import LLMExecutor, LLMSettings


def test_executor_disabled_when_no_key() -> None:
    executor = LLMExecutor(
        LLMSettings(
            enabled=True,
            provider="github",
            model="gpt-4.1-mini",
            api_key=None,
            base_url="https://models.inference.ai.azure.com",
            timeout_ms=8000,
            max_retry=2,
            report_polish_enabled=True,
        )
    )

    assert executor.can_use() is False
    assert executor.suggest_action("Timeout", "svc", "timed out") is None
    assert executor.polish_summary("raw") is None
    assert executor.diagnose_incident({"trace_id": "t1"}) is None
