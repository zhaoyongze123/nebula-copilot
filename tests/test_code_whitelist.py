"""Tests for source code whitelist vector store."""

from pathlib import Path

import pytest

from nebula_copilot.code_whitelist import CodeWhitelistStore, DEFAULT_WHITELIST_DIRS
from nebula_copilot.config import VectorConfig


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository structure."""
    # Create directory structure
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)

    exception_dir = tmp_path / "src" / "exception"
    exception_dir.mkdir(parents=True)

    # Create sample API file
    api_file = api_dir / "handler.py"
    api_file.write_text(
        '''def handle_request(request):
    """Handle incoming request."""
    try:
        result = process(request)
        return {"status": "ok", "data": result}
    except TimeoutException as e:
        return {"status": "timeout", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def process(request):
    """Process request logic."""
    # Call downstream service
    return call_service(request)
'''
    )

    # Create sample exception handling file
    exc_file = exception_dir / "handlers.py"
    exc_file.write_text(
        '''def handle_timeout():
    """Handle timeout exception."""
    # Implement retry logic
    for attempt in range(3):
        try:
            result = call_with_timeout()
            return result
        except TimeoutException:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise

def handle_connection_error(error):
    """Handle connection pool exhaustion."""
    # Implement fallback
    return fallback_result()
'''
    )

    return tmp_path


def test_code_whitelist_store_initialization():
    """Test CodeWhitelistStore can be initialized."""
    store = CodeWhitelistStore(vector_config=VectorConfig(enabled=True, provider="local"))
    assert store.provider in {"local", "custom"}
    assert store.snippet_count == 0


def test_index_from_repository(temp_repo):
    """Test indexing code snippets from repository."""
    whitelist_dirs = {
        "api": ["src/api"],
        "exception_handling": ["src/exception"],
    }
    store = CodeWhitelistStore(
        vector_config=VectorConfig(enabled=True, provider="local"),
        whitelist_dirs=whitelist_dirs,
    )

    indexed = store.index_from_repository(temp_repo)

    # Should index functions with critical keywords
    assert indexed > 0
    assert store.snippet_count > 0


def test_search_code_snippets(temp_repo):
    """Test searching for code snippets."""
    whitelist_dirs = {
        "api": ["src/api"],
        "exception_handling": ["src/exception"],
    }
    store = CodeWhitelistStore(
        vector_config=VectorConfig(
            enabled=True, provider="local", top_k=5, min_score=0.1
        ),
        whitelist_dirs=whitelist_dirs,
    )
    store.index_from_repository(temp_repo)

    # Search for timeout handling code
    matches = store.search(
        service_name="api-service",
        error_type="TimeoutException",
        operation_name="handle",
    )

    assert len(matches) > 0, "Should find timeout-related code"
    # Verify results contain relevant information
    assert any("timeout" in m.keywords or "handle" in m.function_name for m in matches)


def test_search_without_matching_keywords(temp_repo):
    """Test search with non-matching error type returns limited results."""
    whitelist_dirs = {
        "api": ["src/api"],
        "exception_handling": ["src/exception"],
    }
    store = CodeWhitelistStore(
        vector_config=VectorConfig(
            enabled=True, provider="local", top_k=5, min_score=0.1
        ),
        whitelist_dirs=whitelist_dirs,
    )
    store.index_from_repository(temp_repo)

    # Search for unrelated error type
    matches = store.search(
        service_name="payment-service",
        error_type="RandomUnknownError",
    )

    # May return some results based on similarity
    assert isinstance(matches, list)


def test_search_with_vector_disabled():
    """Test search returns empty when vector is disabled."""
    store = CodeWhitelistStore(vector_config=VectorConfig(enabled=False))

    matches = store.search(
        service_name="test-service",
        error_type="TestError",
    )

    assert matches == []


def test_extract_snippets_filters_by_keywords(temp_repo):
    """Test that snippets are filtered by critical keywords."""
    whitelist_dirs = {
        "exception_handling": ["src/exception"],
    }
    store = CodeWhitelistStore(
        vector_config=VectorConfig(enabled=False),  # Disable vector
        whitelist_dirs=whitelist_dirs,
    )

    snippets = []
    exc_dir = temp_repo / "src" / "exception"
    for py_file in exc_dir.rglob("*.py"):
        snippets.extend(
            store._extract_snippets_from_file(py_file, "exception_handling", temp_repo)
        )

    # Should extract snippets with critical keywords
    assert len(snippets) > 0
    assert all(snippet.keywords for snippet in snippets), "All snippets should have keywords"


def test_snippet_contains_metadata(temp_repo):
    """Test that snippets contain required metadata."""
    whitelist_dirs = {
        "api": ["src/api"],
    }
    store = CodeWhitelistStore(
        vector_config=VectorConfig(enabled=False),
        whitelist_dirs=whitelist_dirs,
    )

    snippets = []
    api_dir = temp_repo / "src" / "api"
    for py_file in api_dir.rglob("*.py"):
        snippets.extend(store._extract_snippets_from_file(py_file, "api", temp_repo))

    assert len(snippets) > 0
    snippet = snippets[0]
    assert snippet.snippet_id
    assert snippet.file_path
    assert snippet.function_name
    assert snippet.line_range
    assert snippet.code_text
    assert snippet.category == "api"


def test_code_whitelist_with_empty_repo(tmp_path):
    """Test that whitelist store handles empty repository gracefully."""
    store = CodeWhitelistStore(vector_config=VectorConfig(enabled=True))

    indexed = store.index_from_repository(tmp_path)

    assert indexed == 0
    matches = store.search("service", "error")
    assert matches == []


def test_code_match_structure(temp_repo):
    """Test that code matches have expected structure."""
    whitelist_dirs = {
        "exception_handling": ["src/exception"],
    }
    store = CodeWhitelistStore(
        vector_config=VectorConfig(
            enabled=True, provider="local", top_k=5, min_score=0.1
        ),
        whitelist_dirs=whitelist_dirs,
    )
    store.index_from_repository(temp_repo)

    matches = store.search(
        service_name="payment-service",
        error_type="timeout retry",
    )

    if matches:
        match = matches[0]
        assert match.snippet_id
        assert match.score > 0
        assert match.file_path
        assert match.function_name
        assert match.code_text
        assert isinstance(match.keywords, list)
