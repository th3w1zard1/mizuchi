"""Neutral runtime namespace for the local recovery implementation.

The canonical implementation remains in :mod:`reconkit_re`; this namespace keeps
runtime entrypoints and imports intentionally non-branded for user-facing
integrations.
"""

from __future__ import annotations

from reconkit_re import __all__ as _legacy_all
from reconkit_re import __version__  # noqa: F401

__all__ = list(_legacy_all)
