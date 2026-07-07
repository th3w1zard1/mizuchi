#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat >"$TMP_DIR/inventory.jsonl" <<'JSONL'
{"name":"matched_by_summary","entry":"00401000","section":".textV","bodyBytes":1,"instructionCount":1,"bytes":"c3"}
{"name":"matched_by_manifest","entry":"00401010","section":".textV","bodyBytes":1,"instructionCount":1,"bytes":"c3"}
{"name":"queued","entry":"00401020","section":".textV","bodyBytes":1,"instructionCount":1,"bytes":"c3"}
JSONL

cat >"$TMP_DIR/summary.jsonl" <<'JSONL'
{"name":"matched_by_summary","entry":"00401000","status":"matched","differences":0}
JSONL

cat >"$TMP_DIR/simple_matches.manifest.json" <<'JSON'
{
  "schema": "mizuchi.swkotor-recovered-source-shard.v1",
  "status": "complete",
  "functionCount": 1,
  "functions": [
    {"name": "matched_by_manifest", "entry": "00401010", "exportedSource": "unused.c"}
  ]
}
JSON

"$ROOT/scripts/swkotor-recovery-queue.py" \
  --inventory "$TMP_DIR/inventory.jsonl" \
  --summary "$TMP_DIR/summary.jsonl" \
  --manifest "$TMP_DIR/simple_matches.manifest.json" \
  --out-dir "$TMP_DIR/queue" \
  --limit 10 >/dev/null

jq -e '
  .totalInventoryFunctions == 3 and
  .verifiedMatchedFunctions == 2 and
  .remainingFunctions == 1 and
  .selectedFunctions == 1
' "$TMP_DIR/queue/summary.json" >/dev/null

jq -e '
  length == 1 and .[0].name == "queued" and .[0].entry == "00401020"
' < <(jq -s . "$TMP_DIR/queue/queue.jsonl") >/dev/null

echo "ok"
