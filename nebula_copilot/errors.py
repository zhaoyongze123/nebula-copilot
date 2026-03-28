from __future__ import annotations


class NebulaError(Exception):
    """Base class for user-facing Nebula errors."""


class DataSourceError(NebulaError):
    """Raised when data source operations fail."""


class TraceNotFoundError(DataSourceError):
    """Raised when target trace id is not found in datasource."""


class TraceValidationError(DataSourceError):
    """Raised when trace payload cannot be validated."""
