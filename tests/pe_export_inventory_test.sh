#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${MIZUCHI_KOTOR_BINK_DLL:-/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor/binkw32.dll}"

if [[ ! -f "$TARGET" ]]; then
  echo "skip: KOTOR binkw32.dll not found at $TARGET"
  exit 0
fi

PYTHONPATH="$ROOT/src" python3 - "$TARGET" <<'PY'
import sys
from pathlib import Path

from mizuchi_re.functions import discover_function_candidates
from mizuchi_re.inventory import build_binary_inventory
from mizuchi_re.targets import identify_binary

target = identify_binary(Path(sys.argv[1]))
inventory = build_binary_inventory(target)
candidates = discover_function_candidates(inventory)

exports = inventory.get("exports", [])
assert inventory["format"] == "pe", inventory
assert inventory["summary"]["exports"] >= 1, inventory["summary"]
assert any(row.get("name") == "_BinkGetError@0" for row in exports), exports[:10]

export_candidates = [row for row in candidates["candidates"] if row.get("source") == "pe-export"]
assert len(export_candidates) == inventory["summary"]["exports"], candidates["summary"]
assert all(row.get("confidence") == "high" for row in export_candidates), export_candidates[:5]
assert any(row.get("name") == "_BinkGetError@0" for row in export_candidates), export_candidates[:10]
assert candidates["summary"]["bySource"]["pe-export"] == inventory["summary"]["exports"], candidates["summary"]
PY

echo "ok"
