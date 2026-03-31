"""Tests for Phase 4: Evaluation and governance."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nebula_copilot.evaluation import (
    DataGovernance,
    DataQualityReport,
    MetricsCollector,
    VectorMetrics,
    WeeklyReportBuilder,
)


def test_metrics_collector_initialization():
    """Test MetricsCollector can be initialized."""
    collector = MetricsCollector()
    assert collector.search_count == 0
    assert collector.successful_searches == 0
    assert collector.failed_searches == 0


def test_record_search_operations():
    """Test recording search operations."""
    collector = MetricsCollector()

    collector.record_search(latency_ms=50.0, success=True)
    collector.record_search(latency_ms=100.0, success=True)
    collector.record_search(latency_ms=500.0, success=False)

    assert collector.search_count == 3
    assert collector.successful_searches == 2
    assert collector.failed_searches == 1
    assert collector.total_latency_ms == 650.0


def test_record_adoption():
    """Test recording adoption of suggestions."""
    collector = MetricsCollector()

    collector.record_adoption(correct=True)
    collector.record_adoption(correct=True)
    collector.record_adoption(correct=False)

    assert collector.adopted_suggestions == 3
    assert collector.correct_diagnoses == 2


def test_record_recall_hits():
    """Test recording recall hits."""
    collector = MetricsCollector()

    collector.record_recall("top3")
    collector.record_recall("top3")
    collector.record_recall("top5")

    assert collector.recall_hits["top3"] == 2
    assert collector.recall_hits["top5"] == 1


def test_get_metrics():
    """Test getting aggregated metrics."""
    collector = MetricsCollector()

    # Add some operations
    for _ in range(100):
        collector.record_search(latency_ms=50.0, success=True)
    for _ in range(10):
        collector.record_search(latency_ms=200.0, success=False)

    collector.record_adoption(correct=True)

    metrics = collector.get_metrics()

    assert isinstance(metrics, VectorMetrics)
    assert metrics.total_searches == 110
    assert metrics.successful_searches == 100
    assert metrics.failed_searches == 10
    assert metrics.avg_search_latency_ms > 0


def test_mask_sensitive_data():
    """Test sensitive data masking."""
    text = 'password = "secret123" and token = "abc123xyz"'

    masked, count = DataGovernance.mask_sensitive_data(text)

    assert "secret123" not in masked
    assert "abc123xyz" not in masked
    assert count >= 1
    assert "***password***" in masked or "***" in masked


def test_find_duplicates():
    """Test duplicate detection."""
    records = [
        {
            "service_name": "order-service",
            "error_type": "TimeoutException",
            "summary": "timeout in processing",
        },
        {
            "service_name": "order-service",
            "error_type": "TimeoutException",
            "summary": "timeout in processing",
        },
        {
            "service_name": "payment-service",
            "error_type": "DatabaseError",
            "summary": "db connection failed",
        },
    ]

    duplicates = DataGovernance.find_duplicates(records)

    assert len(duplicates) == 1
    assert duplicates[0] == (0, 1)


def test_cleanup_old_records():
    """Test removing outdated records."""
    now = datetime.now()
    old_date = (now - timedelta(days=100)).isoformat()
    recent_date = (now - timedelta(days=10)).isoformat()

    records = [
        {"timestamp": old_date, "data": "old"},
        {"timestamp": recent_date, "data": "recent"},
        {"timestamp": old_date, "data": "old2"},
    ]

    removed = DataGovernance.cleanup_old_records(records, days=90)

    assert removed == 2


def test_weekly_report_generation():
    """Test weekly report generation."""
    metrics = VectorMetrics(
        timestamp=datetime.now().isoformat(),
        recall_top3=0.75,
        recall_top5=0.88,
        adoption_rate=0.18,
        accuracy_rate=0.85,
        avg_search_latency_ms=80.0,
        total_searches=100,
        successful_searches=95,
        failed_searches=5,
        cost_per_search=0.001,
    )

    quality_report = DataQualityReport(
        timestamp=datetime.now().isoformat(),
        total_records=500,
        duplicates_found=2,
        sensitive_data_found=0,
        outdated_records=10,
        records_cleaned=12,
        retention_policy_applied=True,
    )

    report = WeeklyReportBuilder.build_report(metrics, quality_report)

    assert "metrics" in report
    assert "quality" in report
    assert "recommendations" in report
    assert report["period"] == "weekly"


def test_trend_analysis():
    """Test trend analysis in reports."""
    metrics_current = VectorMetrics(
        timestamp=datetime.now().isoformat(),
        recall_top3=0.75,
        recall_top5=0.88,
        adoption_rate=0.18,
        accuracy_rate=0.85,
        avg_search_latency_ms=80.0,
        total_searches=100,
        successful_searches=95,
        failed_searches=5,
        cost_per_search=0.001,
    )

    metrics_previous = VectorMetrics(
        timestamp=(datetime.now() - timedelta(days=7)).isoformat(),
        recall_top3=0.70,
        recall_top5=0.85,
        adoption_rate=0.15,
        accuracy_rate=0.82,
        avg_search_latency_ms=100.0,
        total_searches=80,
        successful_searches=76,
        failed_searches=4,
        cost_per_search=0.001,
    )

    quality_report = DataQualityReport(
        timestamp=datetime.now().isoformat(),
        total_records=500,
        duplicates_found=0,
        sensitive_data_found=0,
        outdated_records=0,
        records_cleaned=0,
        retention_policy_applied=True,
    )

    report = WeeklyReportBuilder.build_report(
        metrics_current, quality_report, previous_metrics=metrics_previous
    )

    assert "trends" in report
    assert "recall_top3_change" in report["trends"]
    assert float(report["metrics"]["recall"]["top3"].rstrip("%")) > 70


def test_recommendations_generation():
    """Test that recommendations are generated based on metrics."""
    metrics = VectorMetrics(
        timestamp=datetime.now().isoformat(),
        recall_top3=0.60,  # Below target
        recall_top5=0.80,  # Below target
        adoption_rate=0.10,  # Below target
        accuracy_rate=0.70,  # Below target
        avg_search_latency_ms=150.0,  # Above target
        total_searches=100,
        successful_searches=95,
        failed_searches=5,
        cost_per_search=0.001,
    )

    quality_report = DataQualityReport(
        timestamp=datetime.now().isoformat(),
        total_records=500,
        duplicates_found=5,
        sensitive_data_found=2,
        outdated_records=50,
        records_cleaned=0,
        retention_policy_applied=False,
    )

    report = WeeklyReportBuilder.build_report(metrics, quality_report)

    recommendations = report["recommendations"]
    assert len(recommendations) > 0

    # Should have recommendations for low recall, adoption, accuracy, high latency, and data quality issues
    recommendation_text = "\n".join(recommendations)
    assert any(keyword in recommendation_text for keyword in ["recall", "latency", "sensitive", "重复"])
