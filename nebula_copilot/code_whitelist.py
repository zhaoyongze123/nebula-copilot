"""Source code whitelist vector store for code-based diagnosis.

This module extracts code snippets from whitelisted directories and builds
a vector index for code-aware issue diagnosis.

Whitelist Strategy:
- API layers (接口层): Define contracts and error boundaries
- Exception handling (异常处理): Catch and transform exceptions
- Retry/fallback logic (重试降级): Resilience patterns
- Critical paths (关键路径): Hot code paths with high impact
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from nebula_copilot.config import VectorConfig
from nebula_copilot.vector_store import VectorRecord, VectorSearchHit, VectorStore, build_vector_store


# 白名单目录配置
DEFAULT_WHITELIST_DIRS = {
    "api": ["src/api", "app/handlers", "nebula_copilot/tools"],
    "exception_handling": ["src/exception", "app/errors", "nebula_copilot/exceptions"],
    "retry": ["src/retry", "app/resilience", "nebula_copilot/recovery"],
}

# 关键关键字用于识别相关代码
CRITICAL_KEYWORDS = {
    "retry",
    "fallback",
    "timeout",
    "exception",
    "error",
    "catch",
    "handle",
    "circuit",
    "breaker",
    "backoff",
    "limit",
    "pool",
    "connection",
    "deadlock",
}


@dataclass(frozen=True)
class CodeSnippet:
    """Extracted code snippet for indexing."""

    snippet_id: str
    service_name: str
    file_path: str
    function_name: str
    line_range: tuple[int, int]
    code_text: str
    git_commit: Optional[str]
    keywords: List[str]
    dependency_services: List[str]
    category: str  # api, exception_handling, retry, etc.


@dataclass(frozen=True)
class CodeMatch:
    """A matched code snippet with similarity score."""

    snippet_id: str
    score: float
    file_path: str
    function_name: str
    code_text: str
    keywords: List[str]


class CodeWhitelistStore:
    """Vector store for whitelisted source code snippets."""

    def __init__(
        self,
        vector_config: Optional[VectorConfig] = None,
        vector_store: Optional[VectorStore] = None,
        whitelist_dirs: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self._vector_config = vector_config or VectorConfig(
            enabled=True,
            collection_name="nebula_code_whitelist",
            top_k=3,
            min_score=0.35,
        )

        self._vector_store: Optional[VectorStore] = None
        self._vector_provider = "none"
        self._snippets: Dict[str, CodeSnippet] = {}
        self._whitelist_dirs = whitelist_dirs or DEFAULT_WHITELIST_DIRS

        if self._vector_config.enabled:
            if vector_store is not None:
                self._vector_store = vector_store
                self._vector_provider = "custom"
            else:
                build_result = build_vector_store(self._vector_config)
                self._vector_store = build_result.store
                self._vector_provider = build_result.provider

    def index_from_repository(self, repo_root: Path) -> int:
        """Scan and index code snippets from whitelisted directories.

        Args:
            repo_root: Root path of the repository

        Returns:
            Number of snippets indexed
        """
        if not repo_root.exists():
            return 0

        snippets: List[CodeSnippet] = []

        # Scan each whitelist category
        for category, dirs in self._whitelist_dirs.items():
            for dir_pattern in dirs:
                dir_path = repo_root / dir_pattern
                if not dir_path.exists():
                    continue

                # Extract snippets from Python files in this directory
                for py_file in dir_path.rglob("*.py"):
                    extracted = self._extract_snippets_from_file(py_file, category, repo_root)
                    snippets.extend(extracted)

        return self._index_snippets(snippets)

    def search(
        self,
        service_name: str,
        error_type: str,
        operation_name: Optional[str] = None,
    ) -> List[CodeMatch]:
        """Search for relevant code snippets.

        Args:
            service_name: Service name for scoping search
            error_type: Error type to match against
            operation_name: Optional operation name for additional context

        Returns:
            List of matched code snippets sorted by relevance
        """
        if self._vector_store is None:
            return []

        # Build search query
        query_parts = [
            service_name,
            error_type,
        ]
        if operation_name:
            query_parts.append(operation_name)

        # Add keywords that commonly relate to error handling
        if any(keyword in error_type.lower() for keyword in CRITICAL_KEYWORDS):
            related_keywords = [k for k in CRITICAL_KEYWORDS if k in error_type.lower()]
            query_parts.extend(related_keywords)

        query_text = " ".join(query_parts)

        # Perform vector search
        hits = self._vector_store.search(query_text, top_k=self._vector_config.top_k)

        # Transform results
        matches: List[CodeMatch] = []
        for hit in hits:
            if hit.score < self._vector_config.min_score:
                continue

            snippet_id = hit.record_id
            snippet = self._snippets.get(snippet_id)
            if snippet is None:
                continue

            matches.append(
                CodeMatch(
                    snippet_id=snippet_id,
                    score=round(hit.score, 4),
                    file_path=snippet.file_path,
                    function_name=snippet.function_name,
                    code_text=snippet.code_text,
                    keywords=snippet.keywords,
                )
            )

        return matches[: self._vector_config.top_k]

    def _extract_snippets_from_file(
        self, file_path: Path, category: str, repo_root: Path
    ) -> List[CodeSnippet]:
        """Extract code snippets from a Python file."""
        snippets: List[CodeSnippet] = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return snippets

        # Extract functions using simple regex
        # This is a simplified extraction; for production use AST
        function_pattern = r"def\s+(\w+)\s*\([^)]*\):[^\n]*\n((?:    .*\n)*)"
        matches = re.finditer(function_pattern, content)

        relative_path = str(file_path.relative_to(repo_root))
        git_commit = self._get_git_commit(file_path)
        service_name = self._infer_service_name(relative_path)

        for match in matches:
            func_name = match.group(1)
            func_body = match.group(2)
            line_start = content[:match.start()].count("\n") + 1
            line_end = content[:match.end()].count("\n") + 1

            # Filter: Only include functions with critical keywords
            func_text_lower = func_body.lower()
            keywords = [kw for kw in CRITICAL_KEYWORDS if kw in func_text_lower]

            # Skip if no critical keywords
            if not keywords and category != "api":
                continue

            # Limit code length
            code_snippet = func_body[:500]

            # Extract dependency services mentioned in code
            dep_services = self._extract_dependencies(func_body)

            snippet = CodeSnippet(
                snippet_id=self._generate_snippet_id(relative_path, func_name),
                service_name=service_name,
                file_path=relative_path,
                function_name=func_name,
                line_range=(line_start, line_end),
                code_text=code_snippet,
                git_commit=git_commit,
                keywords=keywords if keywords else ["code_snippet"],
                dependency_services=dep_services,
                category=category,
            )
            snippets.append(snippet)

        return snippets

    def _index_snippets(self, snippets: Sequence[CodeSnippet]) -> int:
        """Index snippets into vector store."""
        if self._vector_store is None or not snippets:
            return 0

        records: List[VectorRecord] = []
        for snippet in snippets:
            self._snippets[snippet.snippet_id] = snippet

            # Build indexable text
            text_parts = [
                snippet.service_name,
                snippet.file_path,
                snippet.function_name,
                snippet.category,
                " ".join(snippet.keywords),
                snippet.code_text[:300],
            ]
            text = " ".join(str(p) for p in text_parts if p)

            record = VectorRecord(
                record_id=snippet.snippet_id,
                text=text,
                metadata={
                    "service": snippet.service_name,
                    "file": snippet.file_path,
                    "function": snippet.function_name,
                    "category": snippet.category,
                },
            )
            records.append(record)

        self._vector_store.upsert(records)
        return len(records)

    def _generate_snippet_id(self, file_path: str, func_name: str) -> str:
        """Generate deterministic snippet ID."""
        key = f"{file_path}:{func_name}"
        return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]

    def _get_git_commit(self, file_path: Path) -> Optional[str]:
        """Get latest git commit hash for file."""
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%H", str(file_path)],
                cwd=file_path.parent,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _infer_service_name(self, file_path: str) -> str:
        """Infer service name from file path."""
        # Try to extract from directory structure
        parts = file_path.split("/")
        if len(parts) > 1:
            return parts[0]
        return "unknown"

    def _extract_dependencies(self, code: str) -> List[str]:
        """Extract referenced service names from code."""
        services: Set[str] = set()

        # Look for common service references
        import_pattern = r"from\s+(\w+)\s+import|import\s+(\w+)"
        matches = re.findall(import_pattern, code)
        for match in matches:
            service = match[0] or match[1]
            if service and not service.startswith("_"):
                services.add(service)

        return list(services)[:5]  # Limit to top 5

    @property
    def provider(self) -> str:
        """Return vector store provider name."""
        return self._vector_provider

    @property
    def snippet_count(self) -> int:
        """Return number of indexed snippets."""
        return len(self._snippets)
