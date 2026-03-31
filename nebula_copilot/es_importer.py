"""
ES 批量导入模块：从 Elasticsearch 导入历史 traces 并转换为本地 runs。

功能：
- import_traces()：按时间范围查询 ES 中的 traces
- transform_trace_to_run()：将 TraceDocument 转换为 agent_runs 格式
- save_runs()：保存导入的 runs 到本地 JSON 文件
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nebula_copilot.es_client import search_traces_by_range
from nebula_copilot.models import TraceDocument

logger = logging.getLogger(__name__)


class ImportError(Exception):
    """导入过程中的异常。"""

    pass


class ESImporter:
    """从 Elasticsearch 批量导入 traces 并转换为本地 runs 格式。"""

    def __init__(
        self,
        es_url: str = "http://localhost:9200",
        index: str = "nebula_metrics",
        username: str | None = None,
        password: str | None = None,
        verify_certs: bool = True,
        timeout_seconds: int = 10,
    ) -> None:
        """初始化导入器。

        Args:
            es_url: Elasticsearch 地址
            index: 索引名称或模式
            username: ES 用户名
            password: ES 密码
            verify_certs: 是否验证 SSL 证书
            timeout_seconds: 查询超时（秒）
        """
        self.es_url = es_url
        self.index = index
        self.username = username
        self.password = password
        self.verify_certs = verify_certs
        self.timeout_seconds = timeout_seconds

    def import_traces(
        self,
        from_date: datetime,
        to_date: datetime,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """从 ES 按时间范围导入 traces 并转换为 runs。

        Args:
            from_date: 开始时间（ISO 8601 格式）
            to_date: 结束时间（ISO 8601 格式）
            limit: 最大导入数量

        Returns:
            转换后的 runs 列表

        Raises:
            ImportError: 导入过程中发生错误
        """
        logger.info(f"开始从 ES 导入 traces: {from_date} ~ {to_date}, limit={limit}")

        try:
            traces = search_traces_by_range(
                es_url=self.es_url,
                index=self.index,
                from_date=from_date,
                to_date=to_date,
                limit=limit,
                username=self.username,
                password=self.password,
                verify_certs=self.verify_certs,
                timeout_seconds=self.timeout_seconds,
            )
            logger.info(f"从 ES 查询到 {len(traces)} 条 traces")
        except Exception as exc:
            msg = f"ES 查询失败: {exc}"
            logger.error(msg)
            raise ImportError(msg) from exc

        runs: list[dict[str, Any]] = []
        for i, trace in enumerate(traces):
            try:
                run = self.transform_trace_to_run(trace)
                runs.append(run)
                if (i + 1) % 100 == 0:
                    logger.info(f"已转换 {i + 1}/{len(traces)} traces")
            except Exception as exc:
                logger.warning(f"转换 trace #{i} 失败: {exc}")
                continue

        logger.info(f"成功转换 {len(runs)}/{len(traces)} traces 为 runs")
        return runs

    @staticmethod
    def transform_trace_to_run(trace: TraceDocument) -> dict[str, Any]:
        """将 TraceDocument 转换为 agent_runs 格式。

        转换规则：
        - trace_id → run_id + trace_id
        - 根据导入时间 → started_at + finished_at
        - root.duration_ms → duration_ms (metrics)
        - spans 统计信息 → metrics
        - 最慢的 span 信息 → diagnosis (placeholder)

        Args:
            trace: TraceDocument 对象

        Returns:
            agent_runs 格式的 dict
        """
        if trace.root is None:
            raise ValueError("trace.root cannot be None")

        # 从 trace_id 生成唯一的 run_id
        run_id = f"imported_{hashlib.md5(trace.trace_id.encode()).hexdigest()[:12]}"

        # 使用导入时刻作为时间戳
        now = datetime.now()
        started_at = now.isoformat()
        
        # 根据 duration_ms 计算 finished_at
        duration_ms = trace.root.duration_ms if hasattr(trace.root, "duration_ms") else 0
        finished_at = (now.fromtimestamp(now.timestamp() + duration_ms / 1000)).isoformat()

        # 统计 spans
        span_count = _count_spans(trace.root)
        services = _extract_services(trace.root)

        # 确定状态：根据是否有错误 span
        has_error = _has_error_span(trace.root)
        status = "error" if has_error else "ok"

        # 构建 metrics
        metrics = {
            "duration_ms": max(0, duration_ms),
            "span_count": span_count,
            "service_count": len(services),
            "has_error": has_error,
        }

        # 构建简单的诊断信息
        diagnosis = {
            "type": "imported",
            "bottleneck": {
                "span": {
                    "span_id": trace.root.span_id,
                    "service_name": trace.root.service_name,
                    "duration_ms": duration_ms,
                    "status": status,
                }
            },
            "conclusion": f"Imported trace from ES, duration: {duration_ms}ms, spans: {span_count}",
        }

        # 构建 history（timeline 事件）
        history = _build_timeline(trace.root)

        return {
            "run_id": run_id,
            "trace_id": trace.trace_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "metrics": metrics,
            "diagnosis": diagnosis,
            "history": history,
            "notify": {
                "status": "skipped",
                "sent_at": None,
                "webhook": None,
            },
            "_source": "es_import",
        }

    @staticmethod
    def save_runs(runs: list[dict[str, Any]], output_path: Path) -> None:
        """保存导入的 runs 到本地 JSON 文件（合并现有数据）。

        Args:
            runs: 要保存的 runs 列表
            output_path: 输出文件路径

        Raises:
            ImportError: 保存过程中发生错误
        """
        try:
            # 读取现有的 runs（如果存在）
            existing_runs: list[dict[str, Any]] = []
            if output_path.exists():
                try:
                    data = json.loads(output_path.read_text())
                    if isinstance(data, list):
                        existing_runs = data
                    elif isinstance(data, dict) and "runs" in data:
                        existing_runs = data.get("runs", [])
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"无法解析现有文件 {output_path}，将覆盖")
                    existing_runs = []

            # 按 trace_id 去重（新数据覆盖旧数据）
            existing_by_trace = {run.get("trace_id"): run for run in existing_runs}
            for run in runs:
                existing_by_trace[run.get("trace_id")] = run

            merged_runs = list(existing_by_trace.values())

            # 按 started_at 倒序排列
            merged_runs.sort(key=lambda x: x.get("started_at", ""), reverse=True)

            # 写入文件
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(merged_runs, indent=2, ensure_ascii=False))
            logger.info(f"保存 {len(merged_runs)} 条 runs 到 {output_path}")
        except Exception as exc:
            msg = f"保存 runs 失败: {exc}"
            logger.error(msg)
            raise ImportError(msg) from exc


def _count_spans(span: Any, visited: set[str] | None = None) -> int:
    """递归计算 span 树中的总 span 数（去除重复）。"""
    if visited is None:
        visited = set()

    if span is None:
        return 0

    span_id = getattr(span, "span_id", None)
    if span_id and span_id in visited:
        return 0
    if span_id:
        visited.add(span_id)

    count = 1
    children = getattr(span, "children", None) or []
    for child in children:
        count += _count_spans(child, visited)

    return count


def _extract_services(span: Any, services: set[str] | None = None) -> set[str]:
    """递归提取 span 树中所有不同的 service_name。"""
    if services is None:
        services = set()

    if span is None:
        return services

    service_name = getattr(span, "service_name", None)
    if service_name:
        services.add(service_name)

    children = getattr(span, "children", None) or []
    for child in children:
        _extract_services(child, services)

    return services


def _has_error_span(span: Any) -> bool:
    """递归检查 span 树中是否存在错误 span。"""
    if span is None:
        return False

    status = getattr(span, "status", None)
    if status == "ERROR":
        return True

    children = getattr(span, "children", None) or []
    for child in children:
        if _has_error_span(child):
            return True

    return False


def _build_timeline(span: Any, level: int = 0) -> list[dict[str, Any]]:
    """从 span 树构建 timeline 事件列表。"""
    if span is None:
        return []

    timeline: list[dict[str, Any]] = []

    # 添加 span 本身的事件
    start_time = getattr(span, "start_time", 0)
    end_time = getattr(span, "end_time", start_time)
    duration = end_time - start_time if isinstance(end_time, (int, float)) and isinstance(start_time, (int, float)) else 0

    timeline.append({
        "timestamp": datetime.fromtimestamp(start_time).isoformat() if isinstance(start_time, (int, float)) else datetime.now().isoformat(),
        "level": level,
        "span_id": getattr(span, "span_id", "unknown"),
        "service_name": getattr(span, "service_name", "unknown"),
        "operation_name": getattr(span, "operation_name", "unknown"),
        "duration_ms": int(duration * 1000),
        "status": getattr(span, "status", "UNKNOWN"),
        "event": "span_start",
    })

    # 递归处理子 spans
    children = getattr(span, "children", None) or []
    for child in children:
        timeline.extend(_build_timeline(child, level + 1))

    return timeline
