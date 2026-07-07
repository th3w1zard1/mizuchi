#!/usr/bin/env python3
"""Compatibility wrapper for the packaged source-parity synthesis driver."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mizuchi_re.source_parity_synthesize import main


if __name__ == "__main__":
    raise SystemExit(main())
