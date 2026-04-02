"""
Diagnostic Session Management — 基于 Anthropic 《Effective harnesses for long-running agents》设计。

核心概念：
- DiagnosticSession：一个诊断会话 = Agent 的一个上下文窗口
- DiagnosticManifest：任务清单（对应 Anthropic 的 feature_list.json），所有任务初始为 pending
- SessionManager：会话生命周期管理（创建/恢复/checkpoint/结束）
- Clean State Discipline：每个会话结束写摘要，下一会话从干净状态开始

文件结构：
data/agent_sessions/
  {trace_id}/
    manifest.json         # 任务清单
    checkpoint.json       # 最近一次 checkpoint（AgentState 快照）
    session_summary.txt   # 会话摘要（Anthropic 的 claude-progress.txt）
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# =============================================================================
# 核心数据模型
# =============================================================================


@dataclass
class DiagnosticTask:
    """单个诊断任务 — 对应 Anthropic feature_list.json 中的每个 feature 条目。"""
    task_id: str
    description: str
    status: str = "pending"          # pending | in_progress | completed | failed
    passes: bool = False              # Anthropic 风格：只改 passes，不删除条目
    error: Optional[str] = None       # 失败原因
    completed_at: Optional[str] = None
    retry_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "status": self.status,
            "passes": self.passes,
            "error": self.error,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiagnosticTask":
        return cls(
            task_id=d["task_id"],
            description=d["description"],
            status=d.get("status", "pending"),
            passes=d.get("passes", False),
            error=d.get("error"),
            completed_at=d.get("completed_at"),
            retry_count=d.get("retry_count", 0),
        )


@dataclass
class DiagnosticManifest:
    """诊断任务清单 — 对应 Anthropic 的 feature_list.json。

    关键原则（Anthropic 风格）：
    - 每个任务只有 status 和 passes 字段可变，其他字段不可修改
    - 已完成的任务不能被删除，只能更新 passes
    """
    trace_id: str
    session_id: str
    created_at: str
    updated_at: str
    tasks: List[DiagnosticTask] = field(default_factory=list)
    current_phase: str = "initialized"  # initialized | diagnosing | enriching | reporting | done

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_phase": self.current_phase,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiagnosticManifest":
        return cls(
            trace_id=d["trace_id"],
            session_id=d["session_id"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            current_phase=d.get("current_phase", "initialized"),
            tasks=[DiagnosticTask.from_dict(t) for t in d.get("tasks", [])],
        )


@dataclass
class DiagnosticSession:
    """一个诊断会话 — 对应 Anthropic 的"一个上下文窗口"。

    会话是 Agent 工作的基本单元：
    - 包含当前诊断任务清单（manifest）
    - 引用当前的 AgentState 快照
    - 记录已完成和待处理的任务
    """
    session_id: str
    trace_id: str
    manifest: DiagnosticManifest
    checkpoint_path: Path
    started_at: str
    last_checkpoint_at: Optional[str] = None
    completed_tasks: List[str] = field(default_factory=list)   # 已完成 task_id 列表
    pending_tasks: List[str] = field(default_factory=list)     # 待处理 task_id 列表
    clean_state_summary: Optional[str] = None                  # Anthropic 的 progress summary

    @classmethod
    def new(cls, trace_id: str, sessions_dir: Path) -> "DiagnosticSession":
        """创建新会话 — 对应 Anthropic initializer agent 初始化环境。"""
        now = datetime.now().isoformat(timespec="seconds")
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        task_id_list = [
            "fetch_trace",
            "analyze_bottleneck",
            "classify_error",
            "enrich_jvm",
            "enrich_logs",
            "llm_decision",
            "generate_report",
            "send_notification",
        ]
        tasks = [
            DiagnosticTask(
                task_id=tid,
                description=_TASK_DESCRIPTIONS.get(tid, tid),
            )
            for tid in task_id_list
        ]
        manifest = DiagnosticManifest(
            trace_id=trace_id,
            session_id=session_id,
            created_at=now,
            updated_at=now,
            tasks=tasks,
            current_phase="initialized",
        )
        session_dir = sessions_dir / trace_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            session_id=session_id,
            trace_id=trace_id,
            manifest=manifest,
            checkpoint_path=session_dir / "checkpoint.json",
            started_at=now,
            pending_tasks=list(task_id_list),
            completed_tasks=[],
        )


# =============================================================================
# 任务描述（用于 manifest）
# =============================================================================

_TASK_DESCRIPTIONS: Dict[str, str] = {
    "fetch_trace":       "从 Elasticsearch 拉取 trace 数据",
    "analyze_bottleneck": "分析 trace 识别瓶颈节点",
    "classify_error":    "根据异常栈分类错误类型（Timeout/DB/Downstream/Unknown）",
    "enrich_jvm":        "补充 JVM 指标（heap/gc/p95）",
    "enrich_logs":       "补充相关日志数据",
    "llm_decision":      "LLM 根因决策（可选）",
    "generate_report":   "生成诊断摘要报告",
    "send_notification": "发送告警通知到飞书/钉钉",
}


# =============================================================================
# SessionManager — 会话生命周期管理
# =============================================================================

class SessionManager:
    """会话管理器 — 管理 DiagnosticSession 的完整生命周期。

    Anthropic 风格的关键操作：
    1. 创建会话 → 初始化任务清单
    2. 恢复会话 → 从 checkpoint 加载上下文
    3. Checkpoint  → 保存进度（类比 git commit + progress summary）
    4. 标记完成   → 只更新 passes 字段
    5. 结束会话   → 写 clean_state_summary
    """

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self.sessions_dir: Path = sessions_dir or Path("data/agent_sessions")
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # 生命周期管理
    # -------------------------------------------------------------------------

    def create_session(self, trace_id: str) -> DiagnosticSession:
        """创建新会话 — 对应 Anthropic initializer agent 初始化环境。

        会话目录结构：
          data/agent_sessions/{trace_id}/
            manifest.json         # 任务清单
            checkpoint.json       # checkpoint（如有）
            session_summary.txt   # 进度摘要
        """
        session = DiagnosticSession.new(trace_id, self.sessions_dir)
        self._save_manifest(session)
        self._write_summary(session, "会话已初始化，等待开始诊断。")
        return session

    def load_session(self, trace_id: str) -> DiagnosticSession | None:
        """恢复已有会话 — 对应 Anthropic coding agent 读取上下文。

        读取 manifest.json 和 checkpoint.json，恢复完整的会话状态。
        如果 manifest 不存在，返回 None。
        """
        manifest_path = self.sessions_dir / trace_id / "manifest.json"
        if not manifest_path.exists():
            return None

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = DiagnosticManifest.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

        checkpoint_path = manifest_path.parent / "checkpoint.json"
        checkpoint_data: Dict[str, Any] = {}
        if checkpoint_path.exists():
            try:
                checkpoint_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                checkpoint_data = {}

        # 重建 pending_tasks 和 completed_tasks
        pending = [t.task_id for t in manifest.tasks if t.status == "pending"]
        completed = [t.task_id for t in manifest.tasks if t.status == "completed"]

        # 读取摘要
        summary_path = manifest_path.parent / "session_summary.txt"
        summary = None
        if summary_path.exists():
            summary = summary_path.read_text(encoding="utf-8").strip()

        last_ckpt = checkpoint_data.get("saved_at") if checkpoint_data else None

        return DiagnosticSession(
            session_id=manifest.session_id,
            trace_id=trace_id,
            manifest=manifest,
            checkpoint_path=checkpoint_path,
            started_at=manifest.created_at,
            last_checkpoint_at=last_ckpt,
            completed_tasks=completed,
            pending_tasks=pending,
            clean_state_summary=summary,
        )

    def get_or_create_session(self, trace_id: str) -> DiagnosticSession:
        """获取或创建会话 — 对应 Anthropic 的"先尝试 resume，再决定新建"。

        这是 harness 启动时的主要入口。
        """
        existing = self.load_session(trace_id)
        if existing is not None:
            return existing
        return self.create_session(trace_id)

    # -------------------------------------------------------------------------
    # Checkpoint 与进度管理
    # -------------------------------------------------------------------------

    def checkpoint(self, session: DiagnosticSession, agent_state: Dict[str, Any]) -> None:
        """保存 checkpoint — 对应 Anthropic 的"git commit + progress summary"。

        每次增量步骤完成后调用，写入：
        1. checkpoint.json — AgentState 快照（用于恢复）
        2. manifest.json — 更新任务状态
        """
        now = datetime.now().isoformat(timespec="seconds")

        # 1. 保存 checkpoint
        checkpoint_data = {
            "session_id": session.session_id,
            "trace_id": session.trace_id,
            "saved_at": now,
            "agent_state": agent_state,
            "completed_tasks": session.completed_tasks,
            "pending_tasks": session.pending_tasks,
            "current_phase": session.manifest.current_phase,
        }
        session.checkpoint_path.write_text(
            json.dumps(checkpoint_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        session.last_checkpoint_at = now

        # 2. 更新时间戳
        session.manifest.updated_at = now
        self._save_manifest(session)

    def get_next_task(self, session: DiagnosticSession) -> DiagnosticTask | None:
        """返回下一个 pending 任务 — 对应 Anthropic 的 feature-by-feature 增量执行。

        每次只返回一个待处理任务，强制增量执行。
        """
        for task in session.manifest.tasks:
            if task.status == "pending":
                return task
        return None

    def mark_task_in_progress(self, session: DiagnosticSession, task_id: str) -> None:
        """标记任务开始执行 — 设置 status = in_progress。"""
        for task in session.manifest.tasks:
            if task.task_id == task_id and task.status == "pending":
                task.status = "in_progress"
                session.manifest.updated_at = datetime.now().isoformat(timespec="seconds")
                self._save_manifest(session)
                return

    def mark_task_done(
        self,
        session: DiagnosticSession,
        task_id: str,
        passes: bool,
        error: str | None = None,
    ) -> None:
        """标记任务完成 — Anthropic 风格：只更新 passes 字段，不删除条目。

        passes=True 表示任务成功完成（对应 Anthropic 的 passes:true）
        passes=False 表示任务失败（但仍记录，不删除）
        """
        now = datetime.now().isoformat(timespec="seconds")
        for task in session.manifest.tasks:
            if task.task_id == task_id:
                task.status = "completed"
                task.passes = passes
                task.completed_at = now
                if error:
                    task.error = error
                break

        if task_id in session.pending_tasks:
            session.pending_tasks.remove(task_id)
        if task_id not in session.completed_tasks:
            session.completed_tasks.append(task_id)

        session.manifest.updated_at = now
        self._save_manifest(session)

    def mark_task_failed(
        self,
        session: DiagnosticSession,
        task_id: str,
        error: str,
    ) -> None:
        """标记任务失败 — status=failed，记录错误，累加重试计数。"""
        now = datetime.now().isoformat(timespec="seconds")
        for task in session.manifest.tasks:
            if task.task_id == task_id:
                task.status = "failed"
                task.error = error
                task.retry_count += 1
                task.completed_at = now
                break

        session.manifest.current_phase = "failed"
        session.manifest.updated_at = now
        self._save_manifest(session)

    # -------------------------------------------------------------------------
    # 会话收尾
    # -------------------------------------------------------------------------

    def finalize_session(
        self,
        session: DiagnosticSession,
        agent_state: Dict[str, Any],
        summary_text: str,
    ) -> None:
        """会话结束 — 对应 Anthropic 的 progress summary 写入。

        写入 session_summary.txt，记录本次会话的进度摘要。
        下次 resume 时可快速了解上下文。
        """
        now = datetime.now().isoformat(timespec="seconds")

        # 检查是否全部完成
        all_done = all(t.status in ("completed", "failed") for t in session.manifest.tasks)
        session.manifest.current_phase = "done" if all_done else session.manifest.current_phase
        session.manifest.updated_at = now

        # 生成摘要
        lines = [
            f"[{now}] 会话 {session.session_id} 结束",
            f"Trace: {session.trace_id}",
            f"已完成: {', '.join(session.completed_tasks) or '无'}",
            f"待处理: {', '.join(session.pending_tasks) or '无'}",
            "",
            "=== 会话摘要 ===",
            summary_text,
        ]
        session.clean_state_summary = "\n".join(lines)
        self._write_summary(session, session.clean_state_summary)
        self._save_manifest(session)

        # 最终 checkpoint
        self.checkpoint(session, agent_state)

    # -------------------------------------------------------------------------
    # 会话探查（供 harness 使用）
    # -------------------------------------------------------------------------

    def get_session_info(self, session: DiagnosticSession) -> Dict[str, Any]:
        """返回会话概要信息 — 用于日志和调试。"""
        total = len(session.manifest.tasks)
        completed = len(session.completed_tasks)
        failed = sum(1 for t in session.manifest.tasks if t.status == "failed")
        pending = len(session.pending_tasks)
        return {
            "session_id": session.session_id,
            "trace_id": session.trace_id,
            "progress": f"{completed}/{total} 完成",
            "pending": pending,
            "failed": failed,
            "phase": session.manifest.current_phase,
            "last_checkpoint": session.last_checkpoint_at,
            "summary": session.clean_state_summary,
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有会话目录。"""
        sessions = []
        for trace_dir in self.sessions_dir.iterdir():
            if trace_dir.is_dir():
                session = self.load_session(trace_dir.name)
                if session:
                    sessions.append(self.get_session_info(session))
        return sessions

    # -------------------------------------------------------------------------
    # 私有工具
    # -------------------------------------------------------------------------

    def _save_manifest(self, session: DiagnosticSession) -> None:
        path = self.sessions_dir / session.trace_id / "manifest.json"
        path.write_text(json.dumps(session.manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_summary(self, session: DiagnosticSession, text: str) -> None:
        path = self.sessions_dir / session.trace_id / "session_summary.txt"
        path.write_text(text, encoding="utf-8")
