"""测试 DiagnosticSession / SessionManager / harness 模块。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebula_copilot.agent.session import (
    DiagnosticManifest,
    DiagnosticSession,
    DiagnosticTask,
    SessionManager,
)
from nebula_copilot.agent.state import AgentState


class TestDiagnosticTask:
    def test_to_dict_roundtrip(self) -> None:
        task = DiagnosticTask(
            task_id="analyze_bottleneck",
            description="分析 trace 识别瓶颈",
            status="pending",
            passes=False,
        )
        d = task.to_dict()
        restored = DiagnosticTask.from_dict(d)
        assert restored.task_id == task.task_id
        assert restored.description == task.description
        assert restored.status == "pending"
        assert restored.passes is False

    def test_task_completed_tracks_timestamp(self) -> None:
        task = DiagnosticTask(task_id="fetch_trace", description="拉取 trace", status="pending")
        task.status = "completed"
        task.passes = True
        task.completed_at = "2026-04-02T10:00:00"
        d = task.to_dict()
        assert d["passes"] is True
        assert d["status"] == "completed"


class TestDiagnosticManifest:
    def test_to_dict_roundtrip(self) -> None:
        manifest = DiagnosticManifest(
            trace_id="trace-abc",
            session_id="sess-123",
            created_at="2026-04-02T10:00:00",
            updated_at="2026-04-02T10:00:00",
            tasks=[
                DiagnosticTask(task_id="fetch_trace", description="拉取 trace", status="pending"),
                DiagnosticTask(task_id="analyze", description="分析", status="completed", passes=True),
            ],
        )
        d = manifest.to_dict()
        restored = DiagnosticManifest.from_dict(d)
        assert restored.trace_id == "trace-abc"
        assert len(restored.tasks) == 2
        assert restored.tasks[1].status == "completed"


class TestSessionManager:
    def test_create_session(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.create_session("trace-test-001")

        assert session.session_id.startswith("sess-")
        assert session.trace_id == "trace-test-001"
        assert len(session.manifest.tasks) == 8  # 8 个诊断任务
        assert session.manifest.tasks[0].task_id == "fetch_trace"
        assert session.pending_tasks == ["fetch_trace", "analyze_bottleneck", "classify_error",
                                         "enrich_jvm", "enrich_logs", "llm_decision",
                                         "generate_report", "send_notification"]
        assert session.completed_tasks == []

        # 验证文件已写入
        assert (tmp_path / "trace-test-001" / "manifest.json").exists()
        assert (tmp_path / "trace-test-001" / "session_summary.txt").exists()

    def test_load_session(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        original = manager.create_session("trace-load-001")

        loaded = manager.load_session("trace-load-001")
        assert loaded is not None
        assert loaded.session_id == original.session_id
        assert loaded.trace_id == "trace-load-001"
        assert len(loaded.manifest.tasks) == 8

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        assert manager.load_session("nonexistent-trace") is None

    def test_get_or_create_existing(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        original = manager.create_session("trace-resume-001")
        resumed = manager.get_or_create_session("trace-resume-001")
        assert resumed.session_id == original.session_id

    def test_get_or_create_new(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.get_or_create_session("trace-new-001")
        assert session.trace_id == "trace-new-001"

    def test_get_next_task_returns_pending(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.create_session("trace-next-001")
        task = manager.get_next_task(session)
        assert task is not None
        assert task.task_id == "fetch_trace"
        assert task.status == "pending"

    def test_get_next_task_none_when_all_done(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.create_session("trace-done-001")
        # 标记所有任务完成
        for t in session.manifest.tasks:
            t.status = "completed"
            t.passes = True
        manager._save_manifest(session)
        assert manager.get_next_task(session) is None

    def test_mark_task_done(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.create_session("trace-markdone-001")

        manager.mark_task_done(session, "fetch_trace", passes=True)
        session = manager.load_session("trace-markdone-001")
        fetch_task = next(t for t in session.manifest.tasks if t.task_id == "fetch_trace")
        assert fetch_task.status == "completed"
        assert fetch_task.passes is True
        assert "fetch_trace" in session.completed_tasks
        assert "fetch_trace" not in session.pending_tasks

    def test_mark_task_failed(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.create_session("trace-fail-001")

        manager.mark_task_failed(session, "analyze_bottleneck", error="ES 连接超时")
        session = manager.load_session("trace-fail-001")
        task = next(t for t in session.manifest.tasks if t.task_id == "analyze_bottleneck")
        assert task.status == "failed"
        assert task.error == "ES 连接超时"
        assert task.retry_count == 1

    def test_checkpoint_and_restore(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.create_session("trace-ckpt-001")
        agent_state = AgentState.new(trace_id="trace-ckpt-001", run_id="run-ckpt")
        agent_state.trace_payload = {"trace_id": "trace-ckpt-001", "root": {"span_id": "s1"}}

        manager.checkpoint(session, {
            "run_id": agent_state.run_id,
            "trace_id": agent_state.trace_id,
            "status": agent_state.status,
            "started_at": agent_state.started_at,
            "error": None,
            "trace_payload": {"trace_id": "trace-ckpt-001"},
            "diagnosis": {},
            "jvm_metrics": {},
            "logs": {},
            "summary": None,
            "history": [],
        })

        # 重新创建 session，验证 checkpoint 路径存在
        assert session.checkpoint_path.exists()
        checkpoint_data = json.loads(session.checkpoint_path.read_text())
        assert checkpoint_data["trace_id"] == "trace-ckpt-001"

    def test_finalize_session(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.create_session("trace-fin-001")

        # 模拟部分任务完成
        manager.mark_task_done(session, "fetch_trace", passes=True)
        manager.mark_task_done(session, "analyze_bottleneck", passes=True)

        agent_state = AgentState.new(trace_id="trace-fin-001", run_id="run-fin")
        agent_state.summary = "诊断完成，未发现异常"

        manager.finalize_session(
            session,
            agent_state_to_dict(agent_state),
            summary_text="诊断完成，未发现异常",
        )

        summary_path = tmp_path / "trace-fin-001" / "session_summary.txt"
        assert summary_path.exists()
        summary = summary_path.read_text()
        assert "会话" in summary and "结束" in summary
        assert "fetch_trace" in summary

    def test_list_sessions(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        manager.create_session("trace-list-1")
        manager.create_session("trace-list-2")
        sessions = manager.list_sessions()
        assert len(sessions) == 2

    def test_session_info(self, tmp_path: Path) -> None:
        manager = SessionManager(sessions_dir=tmp_path)
        session = manager.create_session("trace-info-001")
        manager.mark_task_done(session, "fetch_trace", passes=True)

        info = manager.get_session_info(session)
        assert info["trace_id"] == "trace-info-001"
        assert info["progress"] == "1/8 完成"
        assert info["pending"] == 7
        assert info["failed"] == 0


def agent_state_to_dict(state: AgentState) -> dict:
    return {
        "run_id": state.run_id,
        "trace_id": state.trace_id,
        "status": state.status,
        "started_at": state.started_at,
        "error": state.error,
        "trace_payload": state.trace_payload,
        "diagnosis": state.diagnosis,
        "jvm_metrics": state.jvm_metrics,
        "logs": state.logs,
        "summary": state.summary,
        "history": state.history,
    }
