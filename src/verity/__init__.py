"""VERITY — the reality layer for AI coding agents."""

__version__ = "0.2.0"
__tagline__ = "No claim without evidence. No action without reality."
API_VERSION = "1.0"
CONTRACT_SCHEMA_VERSION = 1
DB_SCHEMA_VERSION = 1

from .engine import Engine  # noqa: E402,F401

__all__ = [
    "Engine",
    "__version__",
    "__tagline__",
    "API_VERSION",
    "CONTRACT_SCHEMA_VERSION",
    "DB_SCHEMA_VERSION",
]
