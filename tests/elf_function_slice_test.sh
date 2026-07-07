#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/vvvvvv/VVVVVV"
SYMBOL="_ZN12UtilityClass14hms_to_secondsEiii"

if [[ ! -f "$BINARY" ]]; then
  echo "skip: missing VVVVVV binary: $BINARY"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

"$ROOT/scripts/elf-function-slice.py" scaffold \
  --binary "$BINARY" \
  --symbol "$SYMBOL" \
  --out "$TMP_DIR/scaffold" >/dev/null

cat >"$TMP_DIR/candidate.c" <<'EOF'
__attribute__((used))
int hms_to_seconds(void *self, int h, int m, int s) {
    (void)self;
    return h * 3600 + m * 60 + s;
}
EOF

gcc \
  -x c \
  -std=c99 \
  -O2 \
  -fno-asynchronous-unwind-tables \
  -fno-stack-protector \
  -fno-ident \
  -fno-pic \
  -fno-pie \
  -c "$TMP_DIR/candidate.c" \
  -o "$TMP_DIR/candidate.o"

report="$("$ROOT/scripts/elf-function-slice.py" verify \
  --binary "$BINARY" \
  --symbol "$SYMBOL" \
  --candidate-object "$TMP_DIR/candidate.o" \
  --candidate-symbol hms_to_seconds \
  --out "$TMP_DIR/report.json")"

printf '%s\n' "$report" | jq -e '.status == "matched"' >/dev/null
printf '%s\n' "$report" | jq -e '.byteIdentical == true' >/dev/null
printf '%s\n' "$report" | jq -e '.target.extractedSize == 15' >/dev/null
cmp -s "$TMP_DIR/report.json" <(printf '%s\n' "$report")

echo "ok"
