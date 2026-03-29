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
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_app_config(env_file)

    assert cfg.llm.enabled is True
    assert cfg.llm.api_key == "test-key"
    assert cfg.llm.timeout_ms == 7000
    assert cfg.llm.max_retry == 1
