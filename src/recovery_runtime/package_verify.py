"""Compatibility façade for package verification tooling."""

from __future__ import annotations

from reconkit_re.package_verify import *  # noqa: F401,F403
from reconkit_re.package_verify import main as _main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
