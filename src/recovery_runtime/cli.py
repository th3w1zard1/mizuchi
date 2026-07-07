"""Compatibility façade for the legacy runtime implementation."""

from __future__ import annotations

from reconkit_re.cli import *  # noqa: F401,F403
from reconkit_re.cli import main as _main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
