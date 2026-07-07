"""Compatibility façade for source synthesis tooling."""

from __future__ import annotations

from reconkit_re.source_parity_synthesize import *  # noqa: F401,F403
from reconkit_re.source_parity_synthesize import main as _main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
