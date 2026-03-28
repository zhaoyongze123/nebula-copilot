from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class SpanReport(BaseModel):
    service_name: str
    operation_name: str
    duration_ms: int
    status: str
    error_type: str
    exception_stack: Optional[str] = None
    action_suggestion: str


class NebulaReport(BaseModel):
    trace_id: str
    generated_at: str
    summary: str
    bottleneck: SpanReport
    top_spans: List[SpanReport]
    channel_text: str
