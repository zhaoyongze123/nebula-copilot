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
                "VECTOR_ENABLED=true",
                "VECTOR_PROVIDER=local",
                "VECTOR_TOP_K=5",
                "VECTOR_MIN_SCORE=0.42",
                "VECTOR_COLLECTION=nebula_cases",
                "VECTOR_PERSIST_DIR=/tmp/nebula_vectors",
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
    assert cfg.vector.enabled is True
    assert cfg.vector.provider == "local"
    assert cfg.vector.top_k == 5
    assert cfg.vector.min_score == 0.42
    assert cfg.vector.collection_name == "nebula_cases"
    assert cfg.vector.persist_dir == "/tmp/nebula_vectors"
    assert cfg.run_dedupe_window_seconds == 120
    assert cfg.run_rate_limit_per_minute == 9
    assert str(cfg.run_guard_path) == "data/custom_guard.json"
    assert cfg.metrics_enabled is True


def test_load_app_config_vector_chroma_provider(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "VECTOR_ENABLED=true",
                "VECTOR_PROVIDER=chroma",
                "VECTOR_TOP_K=8",
                "VECTOR_MIN_SCORE=0.65",
                "VECTOR_COLLECTION=trace_patterns",
                "VECTOR_PERSIST_DIR=/var/lib/chroma",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_app_config(env_file)

    assert cfg.vector.enabled is True
    assert cfg.vector.provider == "chroma"
    assert cfg.vector.top_k == 8
    assert cfg.vector.min_score == 0.65
    assert cfg.vector.collection_name == "trace_patterns"
    assert cfg.vector.persist_dir == "/var/lib/chroma"


def test_load_app_config_vector_disabled_by_default(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# No vector config", encoding="utf-8")

    cfg = load_app_config(env_file)

    assert cfg.vector.enabled is False
    assert cfg.vector.provider == "local"
    assert cfg.vector.top_k == 3
    assert cfg.vector.min_score == 0.5
    assert cfg.vector.collection_name == "nebula_kb_patterns"
    assert cfg.vector.persist_dir is None
