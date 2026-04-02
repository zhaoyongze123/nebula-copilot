"""
Agent Harness — 基于 Anthropic 《Effective harnesses for long-running agents》设计。

Harness（调度框架/辎重）是 Agent 运行的外层结构，控制 Agent 如何启动、执行、保存状态。

核心概念：
- build_session_harness()：会话启动协议（Anthropic 的"getting up to speed"）
  1. 检查 ES 健康状态（warmup）
  2. 尝试恢复已有 session
  3. 读取 manifest 和 progress summary
  4. 验证环境干净
- run_incremental_step()：增量执行一步
- run_diagnostic_session()：完整的多步骤诊断会话

这对应 Anthropic 文章中的：
- build_session_harness ≈ "getting up to speed" 协议
- run_incremental_step ≈ "coding agent每次只做一个feature"
- manifest.json ≈ "feature_list.json"
- session_summary.txt ≈ "claude-progress.txt"
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from elasticsearch import Elasticsearch

from nebula_copilot.agent.session import DiagnosticSession, DiagnosticTask, SessionManager
from nebula_copilot.agent.state import AgentState
from nebula_copilot.models import TraceDocument
from nebula_copilot.tools.types import ToolRegistry

logger = logging.getLogger(__name__)

# Checkpoint 保存间隔（秒）：避免过于频繁地写盘
_CHECKPOINT_INTERVAL_SECONDS = 30


# =============================================================================
# ES 健康检查（Anthropic 的 warmup / getting up to speed 第一步）
# =============================================================================


def check_es_health(
    es_url: str,
    index: str,
    username: str | None = None,
    password: str | None = None,
    timeout_seconds: int = 5,
) -> Dict[str, Any]:
    """检查 Elasticsearch 连接和索引状态 — Anthropic 的 warmup 第一步。

    对应 Anthropic 文章中的：
    > "start the development server and run through a basic end-to-end test"

    我们用 ES 健康检查替代，确保诊断环境可用。
    """
    try:
        auth = (username, password) if username and password else None
        es = Elasticsearch(hosts=[es_url], basic_auth=auth, request_timeout=timeout_seconds)

        # 检查集群健康
        health = es.cluster.health(timeout=f"{timeout_seconds}s")

        # 检查索引是否存在且有数据
        index_exists = es.indices.exists(index=index)
        doc_count = 0
        if index_exists:
            stats = es.indices.stats(index=index)
            doc_count = stats["_all"]["primaries"]["docs"]["count"]

        return {
            "status": "ok",
            "cluster_status": health.get("status", "unknown"),
            "index_exists": index_exists,
            "doc_count": doc_count,
            "es_url": es_url,
            "index": index,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": str(exc),
            "es_url": es_url,
            "index": index,
        }


def wait_for_es_ready(
    es_url: str,
    index: str,
    username: str | None = None,
    password: str | None = None,
    max_attempts: int = 3,
    retry_delay: float = 1.0,
) -> Dict[str, Any]:
    """等待 ES 就绪 — 重试 3 次，超过则返回失败状态。"""
    for attempt in range(1, max_attempts + 1):
        result = check_es_health(es_url, index, username, password)
        if result["status"] == "ok":
            return result
        logger.warning(f"ES 健康检查失败（尝试 {attempt}/{max_attempts}）: {result.get('error')}")
        if attempt < max_attempts:
            time.sleep(retry_delay)
    return {"status": "failed", "error": f"ES 在 {max_attempts} 次尝试后仍不可用"}


# =============================================================================
# Agent 上下文构建（Anthropic 的 getting up to speed）
# =============================================================================


def build_session_harness(
    trace_id: str,
    tool_registry: ToolRegistry,
    es_url: str,
    es_index: str,
    es_username: str | None = None,
    es_password: str | None = None,
    sessions_dir: Path | None = None,
    run_id: str | None = None,
    skip_warmup: bool = False,
) -> Dict[str, Any]:
    """会话启动协议 — 对应 Anthropic 的"getting up to speed"阶段。

    执行顺序（对应 Anthropic 的会话启动协议）：
    1. ES 健康检查（warmup）— 确保诊断环境可用
    2. 获取或创建会话（resume or create）
    3. 读取 manifest 任务清单
    4. 读取 session_summary 了解上下文
    5. 验证环境干净（无残留脏状态）

    Returns:
        {
            "session": DiagnosticSession,
            "es_health": Dict,          # ES 健康状态
            "session_info": Dict,       # 会话概要
            "needs_warmup": bool,       # 是否需要 warmup
            "agent_state": AgentState | None,  # 恢复的 AgentState（如有）
        }
    """
    sessions_dir = sessions_dir or Path("data/agent_sessions")
    manager = SessionManager(sessions_dir)

    # Step 1: ES 健康检查（warmup）
    if skip_warmup:
        es_health = {"status": "skipped"}
    else:
        es_health = wait_for_es_ready(es_url, es_index, es_username, es_password)

    # Step 2: 获取或创建会话（Anthropic 的 resume or create）
    session = manager.get_or_create_session(trace_id)

    # Step 3: 从 checkpoint 恢复 AgentState（如有）
    agent_state: AgentState | None = None
    if session.last_checkpoint_at is not None:
        try:
            checkpoint_data = {}
            if session.checkpoint_path.exists():
                import json as _json
                checkpoint_data = _json.loads(session.checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint_data.get("agent_state"):
                ag = checkpoint_data["agent_state"]
                agent_state = AgentState.new(trace_id=trace_id, run_id=ag.get("run_id") or run_id or f"resume-{session.session_id}")
                agent_state.status = ag.get("status", "running")
                agent_state.error = ag.get("error")
                agent_state.trace_payload = ag.get("trace_payload", {})
                agent_state.diagnosis = ag.get("diagnosis", {})
                agent_state.jvm_metrics = ag.get("jvm_metrics", {})
                agent_state.logs = ag.get("logs", {})
                agent_state.summary = ag.get("summary")
                agent_state.history = ag.get("history", [])
                agent_state.started_at = ag.get("started_at", session.started_at)
        except Exception as exc:
            logger.warning(f"从 checkpoint 恢复 AgentState 失败: {exc}，将创建新状态")
            agent_state = None

    if agent_state is None:
        agent_state = AgentState.new(
            trace_id=trace_id,
            run_id=run_id or f"{session.session_id}",
        )

    # Step 4: 获取会话信息
    session_info = manager.get_session_info(session)

    # Step 5: 构建返回结果
    return {
        "session": session,
        "session_manager": manager,
        "es_health": es_health,
        "session_info": session_info,
        "needs_warmup": es_health.get("status") == "unavailable",
        "agent_state": agent_state,
    }


# =============================================================================
# 增量执行步骤（Anthropic 的 feature-by-feature）
# =============================================================================


def run_incremental_step(
    session: DiagnosticSession,
    task: DiagnosticTask,
    agent_state: AgentState,
    session_manager: SessionManager,
    tool_registry: ToolRegistry,
    es_url: str,
    es_index: str,
    es_username: str | None = None,
    es_password: str | None = None,
    llm_executor: Any | None = None,
    llm_decision_required: bool = False,
    history_store: Any = None,
    code_store: Any = None,
) -> DiagnosticSession:
    """增量执行一个诊断步骤 — Anthropic 的 feature-by-feature 模式。

    每个 task 只做一件事：
    - fetch_trace：拉取 trace 数据
    - analyze_bottleneck：分析瓶颈
    - enrich_jvm：补充 JVM 指标
    - enrich_logs：补充日志
    - generate_report：生成报告
    - send_notification：发送通知

    每个步骤完成后自动 checkpoint，失败后可以 resume。
    """
    from nebula_copilot.agent import graph as agent_graph

    manager = session_manager
    trace_id = session.trace_id

    # 标记任务开始
    manager.mark_task_in_progress(session, task.task_id)

    try:
        if task.task_id == "fetch_trace":
            # 拉取 trace（通常已在上一次完成，这里做容错）
            if not agent_state.trace_payload:
                result = _run_with_retry(
                    agent_state,
                    "get_trace",
                    tool_registry.query_trace,
                    trace_id,
                )
                agent_state.trace_payload = result.get("payload", {})
                agent_state.add_event("get_trace", "ok", "trace 拉取完成", result)

        elif task.task_id == "analyze_bottleneck":
            # 分析 trace（如 trace_doc 存在，直接分析）
            if agent_state.trace_payload and "trace_doc" in agent_state.trace_payload:
                trace_doc: TraceDocument = agent_state.trace_payload["trace_doc"]
                agent_graph._node_analyze(agent_state, trace_doc)

        elif task.task_id == "classify_error":
            # 错误分类（在 _node_report 中统一处理）
            bottleneck = agent_state.diagnosis.get("bottleneck", {}) if isinstance(agent_state.diagnosis, dict) else {}
            error_type = str(bottleneck.get("error_type", "Unknown"))
            route = agent_graph._route_error_type(agent_state)
            agent_state.add_event("classify_error", "ok", f"错误分类完成: {error_type}, 路由: {route}")

        elif task.task_id == "enrich_jvm":
            service_name = _extract_bottleneck_service(agent_state)
            if service_name and service_name != "unknown-service":
                agent_graph._node_enrich_jvm(agent_state, tool_registry, service_name)

        elif task.task_id == "enrich_logs":
            service_name = _extract_bottleneck_service(agent_state)
            keyword = _normalize_log_keyword(
                str(agent_state.trace_payload.get("keyword") or "")
            )
            if service_name and service_name != "unknown-service":
                agent_graph._node_enrich_logs(agent_state, tool_registry, service_name, keyword)

        elif task.task_id == "llm_decision":
            service_name = _extract_bottleneck_service(agent_state)
            if llm_executor and llm_decision_required:
                bottleneck = agent_state.diagnosis.get("bottleneck", {}) if isinstance(agent_state.diagnosis, dict) else {}
                error_type = str(bottleneck.get("error_type", "Unknown"))
                operation_name = str(bottleneck.get("operation_name") or "unknown-operation")
                duration_ms = int(bottleneck.get("duration_ms") or 0)
                agent_graph._node_report(
                    agent_state,
                    service_name,
                    llm_executor=llm_executor,
                    llm_decision_required=llm_decision_required,
                    history_store=history_store,
                    code_store=code_store,
                )

        elif task.task_id == "generate_report":
            service_name = _extract_bottleneck_service(agent_state)
            bottleneck = agent_state.diagnosis.get("bottleneck", {}) if isinstance(agent_state.diagnosis, dict) else {}
            error_type = str(bottleneck.get("error_type", "Unknown"))
            operation_name = str(bottleneck.get("operation_name") or "unknown-operation")
            duration_ms = int(bottleneck.get("duration_ms") or 0)
            jvm = agent_state.jvm_metrics if isinstance(agent_state.jvm_metrics, dict) else {}
            logs = agent_state.logs if isinstance(agent_state.logs, dict) else {}
            action_hint = str(bottleneck.get("action_suggestion") or "").strip()

            agent_graph._node_report(
                agent_state,
                service_name,
                llm_executor=llm_executor,
                llm_decision_required=llm_decision_required,
                history_store=history_store,
                code_store=code_store,
            )

        elif task.task_id == "send_notification":
            agent_graph._node_notify(agent_state)

        # 步骤成功完成，标记 passes=True
        manager.mark_task_done(session, task.task_id, passes=True)
        agent_state.add_event(f"step:{task.task_id}", "ok", f"步骤 {task.task_id} 完成")

    except Exception as exc:
        error_msg = str(exc)
        logger.error(f"步骤 {task.task_id} 执行失败: {error_msg}")
        manager.mark_task_failed(session, task.task_id, error=error_msg)
        agent_state.add_event(f"step:{task.task_id}", "failed", error_msg, {"task": task.task_id})
        # 不抛出异常，继续执行下一步骤

    # 自动 checkpoint
    agent_state_dict = _agent_state_to_dict(agent_state)
    manager.checkpoint(session, agent_state_dict)

    return session


# =============================================================================
# 完整诊断会话（整合所有步骤）
# =============================================================================


def run_diagnostic_session(
    trace_id: str,
    tool_registry: ToolRegistry,
    es_url: str,
    es_index: str,
    es_username: str | None = None,
    es_password: str | None = None,
    sessions_dir: Path | None = None,
    run_id: str | None = None,
    llm_executor: Any | None = None,
    llm_decision_required: bool = False,
    history_store: Any = None,
    code_store: Any | None = None,
) -> Dict[str, Any]:
    """完整的多步骤诊断会话 — 对应 Anthropic 的完整 coding session。

    核心循环：
    1. build_session_harness — 会话启动协议
    2. while has pending task:
         run_incremental_step — 增量执行一步
         checkpoint — 保存进度
    3. finalize_session — 写会话摘要

    支持中途失败恢复：
    - kill 进程后重新调用，session 会从上次 checkpoint 恢复
    - 只有 pending 状态的任务会被执行
    """
    # Step 1: 构建 harness（会话启动协议）
    harness_result = build_session_harness(
        trace_id=trace_id,
        tool_registry=tool_registry,
        es_url=es_url,
        es_index=es_index,
        es_username=es_username,
        es_password=es_password,
        sessions_dir=sessions_dir,
        run_id=run_id,
    )

    session: DiagnosticSession = harness_result["session"]
    manager: SessionManager = harness_result["session_manager"]
    agent_state: AgentState = harness_result["agent_state"]
    es_health = harness_result["es_health"]

    # 如果 ES 不可用，记录事件但继续（可能有本地数据）
    if es_health.get("status") == "unavailable":
        agent_state.add_event(
            "warmup",
            "degraded",
            f"ES 健康检查失败: {es_health.get('error')}，尝试使用缓存数据继续",
        )

    # Step 2: 增量执行所有待处理任务
    while True:
        task = manager.get_next_task(session)
        if task is None:
            break

        agent_state.add_event(
            f"step:{task.task_id}",
            "in_progress",
            f"开始执行步骤: {task.task_id} — {task.description}",
        )

        session = run_incremental_step(
            session=session,
            task=task,
            agent_state=agent_state,
            session_manager=manager,
            tool_registry=tool_registry,
            es_url=es_url,
            es_index=es_index,
            es_username=es_username,
            es_password=es_password,
            llm_executor=llm_executor,
            llm_decision_required=llm_decision_required,
            history_store=history_store,
            code_store=code_store,
        )

    # Step 3: 会话结束
    manager.finalize_session(
        session,
        agent_state_dict := _agent_state_to_dict(agent_state),
        summary_text=agent_state.summary or "会话正常结束",
    )

    agent_state.status = "ok"
    agent_state.add_event("session", "done", f"会话 {session.session_id} 正常结束")

    return {
        "session_id": session.session_id,
        "trace_id": trace_id,
        "run_id": agent_state.run_id,
        "status": agent_state.status,
        "started_at": agent_state.started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "es_health": es_health,
        "session_info": manager.get_session_info(session),
        "diagnosis": agent_state.diagnosis,
        "summary": agent_state.summary,
        "history": agent_state.history,
    }


# =============================================================================
# 工具函数
# =============================================================================


def _run_with_retry(state: AgentState, node: str, fn: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    """带重试的节点执行 — 来自 graph.py 的通用逻辑。"""
    from time import sleep as _sleep

    max_retry = 2
    backoff = 0.05
    attempt = 0
    while True:
        attempt += 1
        try:
            result = fn(*args, **kwargs)
            if attempt > 1:
                state.add_event(node, "retry_ok", "重试成功", {"attempt": attempt})
            return result
        except Exception as exc:
            state.add_event(
                node,
                "retry_failed",
                "节点执行失败，准备重试",
                {"attempt": attempt, "error": str(exc)},
            )
            if attempt > max_retry:
                raise
            _sleep(backoff * (2 ** (attempt - 1)))


def _extract_bottleneck_service(state: AgentState) -> str:
    """从诊断结果中提取瓶颈服务名。"""
    if isinstance(state.diagnosis, dict):
        bottleneck = state.diagnosis.get("bottleneck", {})
        if isinstance(bottleneck, dict):
            return str(bottleneck.get("service_name") or "unknown-service")
    return "unknown-service"


def _normalize_log_keyword(raw_keyword: str) -> str:
    """规范化日志关键词 — 来自 graph.py。"""
    keyword = (raw_keyword or "").strip().lower()
    if keyword in {"", "none", "unknown", "ok", "null"}:
        return ""
    return keyword


def _agent_state_to_dict(state: AgentState) -> Dict[str, Any]:
    """将 AgentState 转换为可序列化的 dict（用于 checkpoint）。"""
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
