from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Span(BaseModel):
    span_id: str = Field(..., description="Unique span id")
    parent_span_id: Optional[str] = Field(default=None, description="Parent span id")
    service_name: str = Field(..., description="Service name")
    operation_name: str = Field(..., description="Operation name")
    duration_ms: int = Field(..., ge=0, description="Span duration in milliseconds")
    status: str = Field(default="OK", description="Span status, e.g. OK/ERROR")
    exception_stack: Optional[str] = Field(default=None, description="Exception stack trace")
    children: List["Span"] = Field(default_factory=list)


class TraceDocument(BaseModel):
    trace_id: str
    root: Span
