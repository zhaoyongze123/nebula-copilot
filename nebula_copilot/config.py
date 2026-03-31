from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


def _parse_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}

    env: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


@dataclass
class LLMConfig:
    enabled: bool = False
    provider: str = "github"
    model: str = "gpt-4.1-mini"
    api_key: Optional[str] = None
    base_url: str = "https://models.inference.ai.azure.com"
    timeout_ms: int = 8000
    max_retry: int = 2
    report_polish_enabled: bool = True


@dataclass
class VectorConfig:
    enabled: bool = False
    provider: str = "local"
    top_k: int = 3
    min_score: float = 0.5
    collection_name: str = "nebula_kb_patterns"
    persist_dir: Optional[str] = None


@dataclass
class AppConfig:
    llm: LLMConfig
    vector: VectorConfig = field(default_factory=VectorConfig)
    run_dedupe_window_seconds: int = 300
    run_rate_limit_per_minute: int = 0
    run_guard_path: Path = Path("data/run_guard.json")
    metrics_enabled: bool = True


def load_app_config(env_file: Path | None = None) -> AppConfig:
    env_file = env_file or Path(".env")
    file_env = _parse_env_file(env_file)

    def _get(name: str, default: str = "") -> str:
        from os import getenv

        return getenv(name, file_env.get(name, default))

    llm_enabled = _get("LLM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    llm_provider = _get("LLM_PROVIDER", "github")
    llm_model = _get("LLM_MODEL", "gpt-4.1-mini")
    llm_api_key = _get("GH_MODELS_API_KEY", "")
    llm_base_url = _get("LLM_BASE_URL", "https://models.inference.ai.azure.com")
    llm_timeout_ms = int(_get("LLM_TIMEOUT_MS", "8000") or "8000")
    llm_max_retry = int(_get("LLM_MAX_RETRY", "2") or "2")
    llm_report_polish = _get("LLM_REPORT_POLISH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    vector_enabled = _get("VECTOR_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    vector_provider = _get("VECTOR_PROVIDER", "local")
    vector_top_k = int(_get("VECTOR_TOP_K", "3") or "3")
    vector_min_score = float(_get("VECTOR_MIN_SCORE", "0.5") or "0.5")
    vector_collection_name = _get("VECTOR_COLLECTION", "nebula_kb_patterns")
    vector_persist_dir = _get("VECTOR_PERSIST_DIR", "").strip() or None
    run_dedupe_window_seconds = int(_get("RUN_DEDUPE_WINDOW_SECONDS", "300") or "300")
    run_rate_limit_per_minute = int(_get("RUN_RATE_LIMIT_PER_MINUTE", "0") or "0")
    run_guard_path = Path(_get("RUN_GUARD_PATH", "data/run_guard.json"))
    metrics_enabled = _get("METRICS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

    return AppConfig(
        llm=LLMConfig(
            enabled=llm_enabled,
            provider=llm_provider,
            model=llm_model,
            api_key=llm_api_key or None,
            base_url=llm_base_url,
            timeout_ms=max(1000, llm_timeout_ms),
            max_retry=max(0, llm_max_retry),
            report_polish_enabled=llm_report_polish,
        ),
        vector=VectorConfig(
            enabled=vector_enabled,
            provider=vector_provider,
            top_k=max(1, vector_top_k),
            min_score=min(1.0, max(0.0, vector_min_score)),
            collection_name=vector_collection_name,
            persist_dir=vector_persist_dir,
        ),
        run_dedupe_window_seconds=max(1, run_dedupe_window_seconds),
        run_rate_limit_per_minute=max(0, run_rate_limit_per_minute),
        run_guard_path=run_guard_path,
        metrics_enabled=metrics_enabled,
    )
