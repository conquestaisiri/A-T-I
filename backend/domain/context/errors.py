# backend/domain/context/errors.py
"""Exception hierarchy for the Context Builder domain.

All domain‑level errors inherit from :class:`ContextError`.  Sub‑classes
represent specific failure categories to aid monitoring and recovery.
"""


class ContextError(Exception):
    """Base class for all context‑related errors."""

    pass


class ConfigurationError(ContextError):
    """Raised when a required configuration value is missing or invalid."""

    pass


class DuplicateFeatureError(ContextError):
    """Raised when a feature is registered under a name that already exists."""

    pass


class FeatureRegistrationError(ContextError):
    """Raised when a feature class fails registration validation."""

    pass


class FeatureExecutionError(ContextError):
    """Raised when a feature computation fails unexpectedly."""

    pass


class WindowManagerError(ContextError):
    """Raised for errors in window management (e.g., invalid timestamps)."""

    pass


class EventBusError(ContextError):
    """Raised for infrastructure‑level event bus failures."""

    pass
