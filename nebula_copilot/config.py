from __future__ import annotations

from dataclasses import dataclass
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
class AppConfig:
    llm: LLMConfig
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
        run_dedupe_window_seconds=max(1, run_dedupe_window_seconds),
        run_rate_limit_per_minute=max(0, run_rate_limit_per_minute),
        run_guard_path=run_guard_path,
        metrics_enabled=metrics_enabled,
    )
