from pathlib import Path

from nebula_copilot.config import load_app_config


def test_load_app_config_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LLM_ENABLED=true",
                "LLM_PROVIDER=github",
                "LLM_MODEL=gpt-4.1-mini",
                "GH_MODELS_API_KEY=test-key",
                "LLM_BASE_URL=https://models.inference.ai.azure.com",
                "LLM_TIMEOUT_MS=7000",
                "LLM_MAX_RETRY=1",
                "LLM_REPORT_POLISH_ENABLED=true",
                "RUN_DEDUPE_WINDOW_SECONDS=120",
                "RUN_RATE_LIMIT_PER_MINUTE=9",
                "RUN_GUARD_PATH=data/custom_guard.json",
                "METRICS_ENABLED=true",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_app_config(env_file)

    assert cfg.llm.enabled is True
    assert cfg.llm.api_key == "test-key"
    assert cfg.llm.timeout_ms == 7000
    assert cfg.llm.max_retry == 1
    assert cfg.run_dedupe_window_seconds == 120
    assert cfg.run_rate_limit_per_minute == 9
    assert str(cfg.run_guard_path) == "data/custom_guard.json"
    assert cfg.metrics_enabled is True
