#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHONPATH=src python - <<'PY'
from mizuchi_re.agentdecompile import select_seed_candidates

rows = [
    {
        "name": "FUN_00401000",
        "entryOffset": 0x401000,
        "bodyBytes": 0x5A,
        "section": ".textV",
    },
    {
        "name": "FUN_00401060",
        "entryOffset": 0x401060,
        "bodyBytes": 0x1E,
        "section": ".textV",
    },
]

selected = select_seed_candidates(rows, limit=2)

assert [row["name"] for row in selected] == ["FUN_00401000", "FUN_00401060"], selected
assert selected[0]["address"] == 0x401000, selected
assert selected[0]["endAddress"] == 0x401059, selected
assert selected[1]["address"] == 0x401060, selected
assert selected[1]["endAddress"] == 0x40107D, selected
PY
