from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from nebula_copilot.errors import DataSourceError, TraceNotFoundError, TraceValidationError
from nebula_copilot.es_client import fetch_trace_by_id
from nebula_copilot.models import TraceDocument


class TraceRepository(Protocol):
    def get_trace(self, trace_id: str) -> TraceDocument:
        ...


class LocalJsonRepository:
    """Load a trace document from local JSON file."""

    def __init__(self, source: Path) -> None:
        self.source = source

    def get_trace(self, trace_id: str) -> TraceDocument:
        if not self.source.exists():
            raise DataSourceError(f"数据文件不存在: {self.source}")

        try:
            payload = json.loads(self.source.read_text())
        except json.JSONDecodeError as exc:
            raise TraceValidationError(f"JSON 格式错误: {self.source}") from exc
        except OSError as exc:
            raise DataSourceError(f"读取数据文件失败: {self.source}") from exc

        try:
            trace_doc = TraceDocument.model_validate(payload)
        except ValidationError as exc:
            raise TraceValidationError("Trace 数据字段缺失或类型不合法") from exc

        if trace_doc.trace_id != trace_id:
            raise TraceNotFoundError(f"未找到 trace_id={trace_id}，文件内为 {trace_doc.trace_id}")

        return trace_doc


class ESRepository:
    """Elasticsearch-backed repository implementation."""

    def __init__(
        self,
        es_url: str,
        index: str,
        username: str | None = None,
        password: str | None = None,
        verify_certs: bool = True,
        timeout_seconds: int = 10,
    ) -> None:
        self.es_url = es_url
        self.index = index
        self.username = username
        self.password = password
        self.verify_certs = verify_certs
        self.timeout_seconds = timeout_seconds

    def get_trace(self, trace_id: str) -> TraceDocument:
        return fetch_trace_by_id(
            es_url=self.es_url,
            index=self.index,
            trace_id=trace_id,
            username=self.username,
            password=self.password,
            verify_certs=self.verify_certs,
            timeout_seconds=self.timeout_seconds,
        )


class HTTPRepository:
    """Phase 2 placeholder for HTTP-backed repository."""

    def __init__(self, *_: object, **__: object) -> None:
        pass

    def get_trace(self, trace_id: str) -> TraceDocument:
        raise NotImplementedError(f"HTTPRepository not implemented yet. trace_id={trace_id}")
