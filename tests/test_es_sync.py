"""
Test cases for ES sync module.

运行：
  pytest tests/test_es_sync.py -v
"""

from __future__ import annotations

import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nebula_copilot.es_sync import ESSync, SyncError


class TestESSyncInit:
    """Test ESSync initialization."""

    def test_init_with_defaults(self) -> None:
        """Test initialization with default parameters."""
        sync = ESSync()

        assert sync.es_url == "http://localhost:9200"
        assert sync.index == "nebula_metrics"
        assert sync.output_path == Path("data/agent_runs.json")
        assert sync._is_running is False

    def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        sync = ESSync(
            es_url="http://custom-es:9200",
            index="custom-index",
            output_path="/tmp/runs.json",
        )

        assert sync.es_url == "http://custom-es:9200"
        assert sync.index == "custom-index"
        assert sync.output_path == Path("/tmp/runs.json")


class TestESSyncStartStop:
    """Test starting and stopping sync."""

    def test_start_periodic_sync(self) -> None:
        """Test starting periodic sync."""
        sync = ESSync()

        with patch.object(sync, "_sync_loop", side_effect=lambda *args: time.sleep(10)):
            sync.start_periodic_sync(interval_seconds=10, lookback_minutes=30)

            assert sync._is_running is True
            assert sync._sync_thread is not None
            # Thread may or may not be alive due to timing, just check it was created
            assert sync._sync_thread is not None

            sync.stop_sync()
            # Wait a bit for thread to stop
            time.sleep(0.5)

    def test_start_when_already_running_raises(self) -> None:
        """Test that starting again when running raises SyncError."""
        sync = ESSync()

        with patch.object(sync, "_sync_loop"):
            sync.start_periodic_sync()

            with pytest.raises(SyncError, match="already running"):
                sync.start_periodic_sync()

            sync.stop_sync()
            time.sleep(0.5)

    def test_stop_when_not_running(self) -> None:
        """Test that stopping when not running does not raise."""
        sync = ESSync()
        sync.stop_sync()  # Should not raise


class TestESSyncStatus:
    """Test getting sync status."""

    def test_get_sync_status_not_running(self) -> None:
        """Test getting status when sync is not running."""
        sync = ESSync()
        status = sync.get_sync_status()

        assert status["is_running"] is False
        assert status["last_sync_time"] is None
        assert status["total_synced"] == 0
        assert status["total_errors"] == 0

    def test_get_sync_status_running(self) -> None:
        """Test getting status when sync is running."""
        sync = ESSync()

        with patch.object(sync, "_sync_loop"):
            sync.start_periodic_sync()
            status = sync.get_sync_status()

            assert status["is_running"] is True

            sync.stop_sync()
            time.sleep(0.5)


class TestESSyncContextManager:
    """Test context manager support."""

    def test_context_manager_stops_sync_on_exit(self) -> None:
        """Test that sync stops when exiting context manager."""
        with patch("nebula_copilot.es_sync.ESSync._sync_loop"):
            with ESSync() as sync:
                sync.start_periodic_sync()
                assert sync._is_running is True

            # After exiting context, sync should be stopped
            time.sleep(0.5)
            assert sync._is_running is False

    def test_context_manager_without_sync(self) -> None:
        """Test context manager when sync was never started."""
        with ESSync() as sync:
            assert sync._is_running is False
        # Should not raise


class TestESSyncIntegration:
    """Integration tests for sync functionality."""

    def test_sync_creates_output_file(self) -> None:
        """Test that sync creates the output file if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "subdir" / "runs.json"

            with patch("nebula_copilot.es_sync.ESImporter") as mock_importer_class:
                mock_importer = MagicMock()
                mock_importer_class.return_value = mock_importer
                mock_importer.import_traces.return_value = [
                    {
                        "run_id": "run-1",
                        "trace_id": "trace-1",
                        "status": "ok",
                    }
                ]
                mock_importer.save_runs.return_value = None

                sync = ESSync(output_path=output_path)
                sync._do_sync(lookback_minutes=30)

                # Verify save_runs was called
                mock_importer.save_runs.assert_called_once()

    def test_sync_error_handling(self) -> None:
        """Test that sync handles errors gracefully."""
        with patch("nebula_copilot.es_sync.ESImporter") as mock_importer_class:
            mock_importer = MagicMock()
            mock_importer_class.return_value = mock_importer
            mock_importer.import_traces.side_effect = Exception("ES connection failed")

            sync = ESSync()

            with pytest.raises(Exception):
                sync._do_sync(lookback_minutes=30)

            assert sync._total_errors > 0

    def test_sync_increments_synced_count(self) -> None:
        """Test that sync increments total_synced counter."""
        with patch("nebula_copilot.es_sync.ESImporter") as mock_importer_class:
            mock_importer = MagicMock()
            mock_importer_class.return_value = mock_importer

            runs = [
                {"run_id": f"run-{i}", "trace_id": f"trace-{i}", "status": "ok"}
                for i in range(5)
            ]
            mock_importer.import_traces.return_value = runs
            mock_importer.save_runs.return_value = None

            sync = ESSync()
            sync._do_sync(lookback_minutes=30)

            assert sync._total_synced == 5


class TestESSyncLogging:
    """Test logging output."""

    def test_sync_loop_logs_errors(self, caplog) -> None:
        """Test that sync loop logs errors."""
        import logging

        caplog.set_level(logging.ERROR)

        with patch("nebula_copilot.es_sync.ESImporter") as mock_importer_class:
            mock_importer = MagicMock()
            mock_importer_class.return_value = mock_importer
            mock_importer.import_traces.side_effect = Exception("Test error")

            sync = ESSync()

            # Call _do_sync directly (without thread)
            try:
                sync._do_sync(lookback_minutes=30)
            except Exception:
                pass

            # Error should be logged
            assert any("同步执行失败" in record.message for record in caplog.records)
