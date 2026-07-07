#!/usr/bin/env python3
"""Compatibility wrapper for the source-parity one-shot orchestrator."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from reconkit_re.source_parity_one_shot import main


if __name__ == "__main__":
    raise SystemExit(main())
