"""Compatibility façade for the legacy one-shot front door."""

from __future__ import annotations

from reconkit_re.reconkit_cli import *  # noqa: F401,F403
from reconkit_re.reconkit_cli import main as _main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
