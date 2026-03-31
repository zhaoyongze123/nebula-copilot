"""
ES 自动同步模块：定时从 Elasticsearch 拉取最新 traces 并增量更新本地 runs。

功能：
- start_periodic_sync()：启动后台同步线程
- get_sync_status()：查询同步统计信息
- stop_sync()：停止后台同步
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nebula_copilot.es_importer import ESImporter

logger = logging.getLogger(__name__)


class SyncError(Exception):
    """同步过程中的异常。"""

    pass


class ESSync:
    """从 Elasticsearch 定时同步 traces 并增量更新本地 runs。"""

    def __init__(
        self,
        es_url: str = "http://localhost:9200",
        index: str = "nebula_metrics",
        output_path: Path | str = "data/agent_runs.json",
        username: str | None = None,
        password: str | None = None,
        verify_certs: bool = True,
        timeout_seconds: int = 10,
    ) -> None:
        """初始化同步器。

        Args:
            es_url: Elasticsearch 地址
            index: 索引名称或模式
            output_path: 本地 runs 文件路径
            username: ES 用户名
            password: ES 密码
            verify_certs: 是否验证 SSL 证书
            timeout_seconds: 查询超时（秒）
        """
        self.es_url = es_url
        self.index = index
        self.output_path = Path(output_path)
        self.username = username
        self.password = password
        self.verify_certs = verify_certs
        self.timeout_seconds = timeout_seconds

        self._importer = ESImporter(
            es_url=es_url,
            index=index,
            username=username,
            password=password,
            verify_certs=verify_certs,
            timeout_seconds=timeout_seconds,
        )

        # 同步状态
        self._sync_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_sync_time: datetime | None = None
        self._total_synced = 0
        self._total_errors = 0
        self._is_running = False

    def start_periodic_sync(
        self,
        interval_seconds: int = 300,
        lookback_minutes: int = 60,
    ) -> None:
        """启动后台定期同步。

        Args:
            interval_seconds: 同步间隔（秒），默认 300s（5 分钟）
            lookback_minutes: 回溯窗口（分钟），默认 60 分钟
                - 每次同步拉取最近 lookback_minutes 的 traces
                - 配合去重确保不重复导入

        Raises:
            SyncError: 同步已在运行或启动失败
        """
        with self._lock:
            if self._is_running:
                raise SyncError("Sync is already running")

            self._is_running = True
            self._stop_event.clear()

        logger.info(f"启动 ES 同步，间隔 {interval_seconds}s，回溯 {lookback_minutes} 分钟")

        self._sync_thread = threading.Thread(
            target=self._sync_loop,
            args=(interval_seconds, lookback_minutes),
            daemon=True,
            name="ESSync",
        )
        self._sync_thread.start()

    def _sync_loop(self, interval_seconds: int, lookback_minutes: int) -> None:
        """后台同步循环。

        Args:
            interval_seconds: 同步间隔
            lookback_minutes: 回溯窗口
        """
        while not self._stop_event.wait(timeout=interval_seconds):
            try:
                self._do_sync(lookback_minutes)
                with self._lock:
                    self._last_sync_time = datetime.now()
            except Exception as exc:
                logger.error(f"同步失败: {exc}")
                with self._lock:
                    self._total_errors += 1

        logger.info("ES 同步已停止")

    def _do_sync(self, lookback_minutes: int) -> None:
        """执行一次同步。

        Args:
            lookback_minutes: 回溯窗口

        Raises:
            SyncError: 同步失败
        """
        now = datetime.now()
        from_date = now - timedelta(minutes=lookback_minutes)
        to_date = now

        logger.info(f"执行同步: {from_date.isoformat()} ~ {to_date.isoformat()}")

        try:
            # 从 ES 导入 traces
            runs = self._importer.import_traces(
                from_date=from_date,
                to_date=to_date,
                limit=500,  # 单次导入上限
            )

            if runs:
                # 保存到本地（自动去重和合并）
                self._importer.save_runs(runs, self.output_path)
                with self._lock:
                    self._total_synced += len(runs)
                logger.info(f"同步完成: 导入 {len(runs)} 条 traces")
            else:
                logger.info("同步完成: 无新数据")

        except Exception as exc:
            logger.error(f"同步执行失败: {exc}")
            with self._lock:
                self._total_errors += 1
            raise

    def stop_sync(self) -> None:
        """停止后台同步。

        Returns:
            同步完成后返回；如果同步未运行则立即返回

        Raises:
            SyncError: 停止失败
        """
        with self._lock:
            if not self._is_running:
                logger.warning("Sync is not running")
                return
            self._is_running = False

        logger.info("正在停止 ES 同步...")
        self._stop_event.set()

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10)
            if self._sync_thread.is_alive():
                logger.warning("Sync thread did not stop in time")

    def get_sync_status(self) -> dict[str, Any]:
        """获取同步状态。

        Returns:
            包含同步统计的 dict：
            - is_running: 是否正在运行
            - last_sync_time: 最后一次同步时间（ISO 8601）
            - total_synced: 累计导入数
            - total_errors: 累计错误数
            - uptime_seconds: 运行时长（秒）
        """
        with self._lock:
            status = {
                "is_running": self._is_running,
                "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
                "total_synced": self._total_synced,
                "total_errors": self._total_errors,
            }

        return status

    def __enter__(self) -> ESSync:
        """上下文管理器支持。

        Returns:
            self
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """上下文管理器退出时停止同步。

        Args:
            exc_type: 异常类型
            exc_val: 异常值
            exc_tb: 异常回溯
        """
        self.stop_sync()
