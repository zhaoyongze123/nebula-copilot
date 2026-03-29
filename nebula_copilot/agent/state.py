from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class AgentState:
    trace_id: str
    run_id: str
    started_at: str
    status: str = "running"
    error: Optional[str] = None
    trace_payload: Dict[str, Any] = field(default_factory=dict)
    diagnosis: Dict[str, Any] = field(default_factory=dict)
    jvm_metrics: Dict[str, Any] = field(default_factory=dict)
    logs: Dict[str, Any] = field(default_factory=dict)
    summary: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def new(cls, trace_id: str, run_id: str) -> "AgentState":
        return cls(
            trace_id=trace_id,
            run_id=run_id,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )

    def add_event(self, node: str, status: str, detail: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.history.append(
            {
                "node": node,
                "status": status,
                "detail": detail,
                "payload": payload or {},
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )
