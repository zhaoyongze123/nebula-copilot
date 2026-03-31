"""Phase 4: Evaluation and governance for vector retrieval system.

This module provides:
1. Quality metrics tracking (recall rate, adoption rate, accuracy)
2. Performance monitoring (latency, costs)
3. Data governance (cleanup, deduplication, sensitive data masking)
4. Weekly/monthly reporting and trend analysis
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class VectorMetrics:
    """Metrics for vector retrieval performance."""

    timestamp: str
    recall_top3: float  # Top-3 recall rate
    recall_top5: float  # Top-5 recall rate
    adoption_rate: float  # % of suggestions adopted
    accuracy_rate: float  # % of correct diagnoses
    avg_search_latency_ms: float
    total_searches: int
    successful_searches: int
    failed_searches: int
    cost_per_search: float


@dataclass
class DataQualityReport:
    """Report on data quality and governance."""

    timestamp: str
    total_records: int
    duplicates_found: int
    sensitive_data_found: int
    outdated_records: int
    records_cleaned: int
    retention_policy_applied: bool


@dataclass
class MetricsCollector:
    """Collects and aggregates vector retrieval metrics."""

    start_time: datetime = field(default_factory=datetime.now)
    search_count: int = 0
    successful_searches: int = 0
    failed_searches: int = 0
    total_latency_ms: float = 0.0
    adopted_suggestions: int = 0
    correct_diagnoses: int = 0
    recall_hits: Dict[str, int] = field(default_factory=lambda: {"top3": 0, "top5": 0})

    def record_search(self, latency_ms: float, success: bool = True) -> None:
        """Record a search operation."""
        self.search_count += 1
        self.total_latency_ms += latency_ms
        if success:
            self.successful_searches += 1
        else:
            self.failed_searches += 1

    def record_adoption(self, correct: bool = True) -> None:
        """Record that a suggestion was adopted."""
        self.adopted_suggestions += 1
        if correct:
            self.correct_diagnoses += 1

    def record_recall(self, rank: str) -> None:
        """Record a recall hit (top3 or top5)."""
        if rank in self.recall_hits:
            self.recall_hits[rank] += 1

    def get_metrics(self) -> VectorMetrics:
        """Get current aggregated metrics."""
        success_rate = (
            self.successful_searches / max(self.search_count, 1) if self.search_count > 0 else 0.0
        )
        avg_latency = self.total_latency_ms / max(self.search_count, 1) if self.search_count > 0 else 0.0

        # Calculate recall rates (simplified: assume all successful searches have results)
        recall_top3 = self.recall_hits.get("top3", 0) / max(self.successful_searches, 1)
        recall_top5 = self.recall_hits.get("top5", 0) / max(self.successful_searches, 1)

        adoption_rate = self.adopted_suggestions / max(self.successful_searches, 1)
        accuracy_rate = self.correct_diagnoses / max(self.adopted_suggestions, 1)

        return VectorMetrics(
            timestamp=datetime.now().isoformat(),
            recall_top3=min(1.0, recall_top3),
            recall_top5=min(1.0, recall_top5),
            adoption_rate=min(1.0, adoption_rate),
            accuracy_rate=min(1.0, accuracy_rate),
            avg_search_latency_ms=avg_latency,
            total_searches=self.search_count,
            successful_searches=self.successful_searches,
            failed_searches=self.failed_searches,
            cost_per_search=0.0,  # Placeholder for actual cost calculation
        )


class DataGovernance:
    """Manages data governance for vector stores."""

    # Sensitive patterns to detect
    SENSITIVE_PATTERNS = {
        "password": r"(?i)password\s*[=:]\s*['\"]([^'\"]*)['\"]",
        "token": r"(?i)token\s*[=:]\s*['\"]([^'\"]*)['\"]",
        "secret": r"(?i)secret\s*[=:]\s*['\"]([^'\"]*)['\"]",
        "api_key": r"(?i)api[_-]key\s*[=:]\s*['\"]([^'\"]*)['\"]",
    }

    # Retention policy: keep records for 90 days
    RETENTION_DAYS = 90

    @staticmethod
    def mask_sensitive_data(text: str) -> tuple[str, int]:
        """Mask sensitive data in text.

        Args:
            text: Text to mask

        Returns:
            Tuple of (masked_text, number_of_replacements)
        """
        import re

        masked_text = text
        count = 0

        for pattern_type, pattern in DataGovernance.SENSITIVE_PATTERNS.items():
            # Find and mask matches
            matches = re.finditer(pattern, masked_text)
            for match in matches:
                # Replace with placeholder
                old_value = match.group(1)
                masked = f"***{pattern_type}***"
                masked_text = masked_text.replace(old_value, masked)
                count += 1

        return masked_text, count

    @staticmethod
    def find_duplicates(records: List[Dict[str, Any]]) -> List[tuple[int, int]]:
        """Find potential duplicate records.

        Args:
            records: List of records to check

        Returns:
            List of (index1, index2) tuples of potential duplicates
        """
        duplicates: List[tuple[int, int]] = []
        seen_hashes: Dict[str, int] = {}

        for i, record in enumerate(records):
            # Use key fields for deduplication
            key_fields = ("service_name", "error_type", "summary")
            key = tuple(record.get(field, "") for field in key_fields)
            key_hash = hash(key)

            if key_hash in seen_hashes:
                duplicates.append((seen_hashes[key_hash], i))
            else:
                seen_hashes[key_hash] = i

        return duplicates

    @staticmethod
    def cleanup_old_records(records: List[Dict[str, Any]], days: int = RETENTION_DAYS) -> int:
        """Remove records older than retention period.

        Args:
            records: List of records to filter
            days: Number of days to retain

        Returns:
            Number of records removed
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        initial_count = len(records)

        filtered = []
        for record in records:
            timestamp_str = record.get("timestamp", "")
            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                if timestamp > cutoff_date:
                    filtered.append(record)
            except Exception:
                # Keep records with invalid timestamps
                filtered.append(record)

        removed = initial_count - len(filtered)
        return removed


class WeeklyReportBuilder:
    """Builds weekly evaluation reports."""

    @staticmethod
    def build_report(
        metrics: VectorMetrics,
        quality_report: DataQualityReport,
        previous_metrics: Optional[VectorMetrics] = None,
    ) -> Dict[str, Any]:
        """Build a weekly evaluation report.

        Args:
            metrics: Current metrics
            quality_report: Data quality report
            previous_metrics: Previous week's metrics for trend analysis

        Returns:
            Report dictionary
        """
        report = {
            "timestamp": datetime.now().isoformat(),
            "period": "weekly",
            "metrics": {
                "recall": {
                    "top3": f"{metrics.recall_top3:.2%}",
                    "top5": f"{metrics.recall_top5:.2%}",
                    "target": "≥70% (top3), ≥85% (top5)",
                },
                "adoption": {
                    "rate": f"{metrics.adoption_rate:.2%}",
                    "accuracy": f"{metrics.accuracy_rate:.2%}",
                    "target": "≥15% adoption, ≥80% accuracy",
                },
                "performance": {
                    "avg_latency_ms": f"{metrics.avg_search_latency_ms:.1f}",
                    "success_rate": f"{metrics.successful_searches / max(metrics.total_searches, 1):.2%}",
                    "target": "<120ms (local), <250ms (prod); >95% success",
                },
                "operations": {
                    "total_searches": metrics.total_searches,
                    "successful": metrics.successful_searches,
                    "failed": metrics.failed_searches,
                },
            },
            "quality": {
                "total_records": quality_report.total_records,
                "duplicates_found": quality_report.duplicates_found,
                "sensitive_data_found": quality_report.sensitive_data_found,
                "outdated_records": quality_report.outdated_records,
                "records_cleaned": quality_report.records_cleaned,
            },
            "trends": {},
            "recommendations": WeeklyReportBuilder._generate_recommendations(metrics, quality_report),
        }

        # Add trend analysis if previous metrics available
        if previous_metrics:
            report["trends"] = {
                "recall_top3_change": f"{(metrics.recall_top3 - previous_metrics.recall_top3):.2%}",
                "adoption_change": f"{(metrics.adoption_rate - previous_metrics.adoption_rate):.2%}",
                "latency_change_ms": f"{metrics.avg_search_latency_ms - previous_metrics.avg_search_latency_ms:.1f}",
            }

        return report

    @staticmethod
    def _generate_recommendations(
        metrics: VectorMetrics, quality_report: DataQualityReport
    ) -> List[str]:
        """Generate actionable recommendations based on metrics."""
        recommendations = []

        # Recall performance
        if metrics.recall_top3 < 0.70:
            recommendations.append(
                "⚠️  Top-3 recall低于70%目标，考虑调整向量模型或扩大索引范围"
            )
        if metrics.recall_top5 < 0.85:
            recommendations.append(
                "⚠️  Top-5 recall低于85%目标，考虑增加索引覆盖度或优化向量评分策略"
            )

        # Adoption performance
        if metrics.adoption_rate < 0.15:
            recommendations.append(
                "💡 建议采纳率低于15%目标，可能需要改进建议质量或用户交互设计"
            )
        if metrics.accuracy_rate < 0.80:
            recommendations.append(
                "🔧 诊断准确率低于80%，需要增加训练数据或改进规则模型"
            )

        # Latency performance
        if metrics.avg_search_latency_ms > 120:
            recommendations.append(
                "⏱️  搜索延迟>120ms，可考虑缓存热点查询或优化向量搜索算法"
            )

        # Data quality
        if quality_report.duplicates_found > 0:
            recommendations.append(
                f"🗑️  发现{quality_report.duplicates_found}个重复记录，需要运行去重任务"
            )
        if quality_report.sensitive_data_found > 0:
            recommendations.append(
                f"🔐 发现{quality_report.sensitive_data_found}条敏感信息，需要立即脱敏处理"
            )
        if quality_report.outdated_records > 0:
            recommendations.append(
                f"📦 有{quality_report.outdated_records}条过期记录，按保留策略清理"
            )

        return recommendations


def create_sample_metrics_report(output_file: Path) -> None:
    """Create a sample metrics report for demonstration.

    Args:
        output_file: Path to write report
    """
    collector = MetricsCollector()

    # Simulate some search operations
    for _ in range(100):
        collector.record_search(latency_ms=50.0, success=True)
    for _ in range(10):
        collector.record_search(latency_ms=200.0, success=False)

    # Simulate adoptions
    for _ in range(10):
        collector.record_adoption(correct=True)
    for _ in range(2):
        collector.record_adoption(correct=False)

    # Simulate recalls
    for _ in range(70):
        collector.record_recall("top3")
    for _ in range(15):
        collector.record_recall("top5")

    metrics = collector.get_metrics()

    quality_report = DataQualityReport(
        timestamp=datetime.now().isoformat(),
        total_records=500,
        duplicates_found=3,
        sensitive_data_found=1,
        outdated_records=20,
        records_cleaned=24,
        retention_policy_applied=True,
    )

    report = WeeklyReportBuilder.build_report(metrics, quality_report)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"✅ 样本报告已写入: {output_file}")
