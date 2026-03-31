#!/usr/bin/env python
"""Offline script to build and update history vector index.

Usage:
    python scripts/build_history_index.py [--runs-file PATH] [--output-dir DIR]

This script:
1. Reads historical run records from agent_runs.json
2. Extracts structured diagnosis cases
3. Builds vector index for similarity-based retrieval
4. Validates index quality (min recall tests)
5. Persists index for production use
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from nebula_copilot.config import VectorConfig
from nebula_copilot.history_vector import HistoryVectorStore


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Build history vector index from agent run records"
    )
    parser.add_argument(
        "--runs-file",
        type=Path,
        default=Path("data/agent_runs.json"),
        help="Path to agent_runs.json file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/history_index"),
        help="Output directory for vector index (for Chroma persistence)",
    )
    parser.add_argument(
        "--provider",
        choices=["local", "chroma"],
        default="local",
        help="Vector store provider",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of results to retrieve per search",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.4,
        help="Minimum similarity score threshold",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run validation tests on the index",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    return parser.parse_args()


def log(msg: str, verbose: bool = False) -> None:
    """Print log message."""
    if not verbose:
        return
    print(f"[build_history_index] {msg}", file=sys.stderr)


def validate_index(store: HistoryVectorStore) -> bool:
    """Validate index quality with sample recalls.

    Returns:
        True if validation passes, False otherwise.
    """
    # Sample test cases for validation
    test_cases = [
        {
            "service": "order-service",
            "error_type": "TimeoutException",
            "description": "超时异常",
            "expect_hit": True,
        },
        {
            "service": "payment-service",
            "error_type": "DatabaseException",
            "description": "数据库异常",
            "expect_hit": True,
        },
    ]

    passed = 0
    total = len(test_cases)

    print("\n[验证] 运行召回测试...")
    for test in test_cases:
        matches = store.search(
            service_name=test["service"],
            operation_name="test-operation",
            error_type=test["error_type"],
        )

        has_hit = len(matches) > 0
        status = "✓" if has_hit == test["expect_hit"] else "✗"

        print(
            f"  {status} {test['description']}: "
            f"找到 {len(matches)} 个匹配 (期望: {'有' if test['expect_hit'] else '无'})"
        )

        if has_hit == test["expect_hit"]:
            passed += 1

    return passed == total


def build_index(args: argparse.Namespace) -> int:
    """Build the history vector index.

    Returns:
        0 on success, 1 on failure.
    """
    # Validate input file
    if not args.runs_file.exists():
        print(f"❌ 错误: 找不到输入文件 {args.runs_file}", file=sys.stderr)
        return 1

    print(f"📖 读取运行记录: {args.runs_file}")

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize vector store
    persist_dir = str(args.output_dir) if args.provider == "chroma" else None
    vector_config = VectorConfig(
        enabled=True,
        provider=args.provider,
        collection_name="nebula_diagnosis_history",
        top_k=args.top_k,
        min_score=args.min_score,
        persist_dir=persist_dir,
    )

    print(f"🔧 初始化向量库: provider={args.provider}, top_k={args.top_k}, min_score={args.min_score}")

    store = HistoryVectorStore(vector_config=vector_config)

    # Index runs file
    print(f"📝 索引历史案例...")
    indexed = store.index_from_runs_file(args.runs_file)
    print(f"✅ 成功索引 {indexed} 个案例 (库类型: {store.provider})")

    if indexed == 0:
        print("⚠️  警告: 没有可索引的案例，请检查输入文件格式", file=sys.stderr)

    # Write metadata
    metadata = {
        "indexed_count": indexed,
        "provider": store.provider,
        "top_k": args.top_k,
        "min_score": args.min_score,
        "source_file": str(args.runs_file.resolve()),
    }

    metadata_file = args.output_dir / "metadata.json"
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"📄 写入元数据: {metadata_file}")

    # Run validation if requested
    if args.validate:
        if not validate_index(store):
            print("⚠️  验证失败，但继续构建", file=sys.stderr)

    print(f"\n✨ 向量库构建完成!")
    print(f"   • 索引位置: {args.output_dir}")
    print(f"   • 案例数量: {indexed}")
    print(f"   • 库类型: {store.provider}")

    return 0


def main() -> int:
    """Main entry point."""
    args = parse_args()
    try:
        return build_index(args)
    except Exception as exc:
        print(f"❌ 错误: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
