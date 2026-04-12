"""Shared exceptions for standalone module validation."""

from __future__ import annotations


class ModuleValidationError(ValueError):
    """Raised when module inputs violate formal constraints."""


class StabilityViolationError(RuntimeError):
    """Raised when contraction/drift/resonance checks fail."""
