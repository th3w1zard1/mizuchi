#!/bin/bash
# run-permuter.sh — Run decomp-permuter for a Klonoa function from CLI
#
# Usage:
#   ./scripts/run-permuter.sh <function-name> <base-c-file> [flags...]
#
# Example:
#   ./scripts/run-permuter.sh EntityItemDrop /tmp/base.c --show-errors -j 8
#
# The base C file should contain ONLY the function body (no headers).
# ctx.c from the Klonoa project is prepended automatically.
#
# Hardcoded for the klonoa-empire-of-dreams project.

set -euo pipefail

FUNC_NAME="${1:?Usage: $0 <function-name> <base-c-file> [permuter-flags...]}"
BASE_C="${2:?Usage: $0 <function-name> <base-c-file> [permuter-flags...]}"
shift 2
PERMUTER_FLAGS=("$@")

# Paths (hardcoded for this project)
PROJ="/Users/macabeus/ApenasMeu/decompiler/klonoa-empire-of-dreams"
MIZUCHI="/Users/macabeus/ApenasMeu/decompiler/mizuchi"
PERMUTER_DIR="$MIZUCHI/vendor/decomp-permuter"
PERMUTER_PYTHON="$PERMUTER_DIR/.venv/bin/python"
TARGET_OBJ="$PROJ/build/src/code_1.o"

# Ensure the target .o exists (build with INCLUDE_ASM)
if [ ! -f "$TARGET_OBJ" ]; then
    echo "Building target .o..."
    (cd "$PROJ" && make build/src/code_1.o)
fi

# Create working directory
WORKDIR=$(mktemp -d -t mizuchi-permuter-XXXXXX)
trap "rm -rf $WORKDIR" EXIT
echo "Working directory: $WORKDIR"

# 1. Write context.h from ctx.c
cp "$PROJ/ctx.c" "$WORKDIR/context.h"

# 2. Write base.c (context include + function code)
echo '#include "context.h"' > "$WORKDIR/base.c"
cat "$BASE_C" >> "$WORKDIR/base.c"

# 3. Copy target.o
cp "$TARGET_OBJ" "$WORKDIR/target.o"

# 4. Write settings.toml
cat > "$WORKDIR/settings.toml" << TOMLEOF
func_name = "$FUNC_NAME"
compiler_type = "gcc"
objdump_command = "$WORKDIR/objdump_wrapper.sh -drz"
TOMLEOF

# 5. Write objdump wrapper (extracts only the target function from multi-function .o)
cat > "$WORKDIR/objdump_wrapper.sh" << 'OBJEOF'
#!/bin/bash
NM_CMD="arm-none-eabi-nm"
OBJDUMP_CMD="arm-none-eabi-objdump"

ARGS=("$@")
OBJ_FILE="${ARGS[${#ARGS[@]}-1]}"
OBJDUMP_ARGS=("${ARGS[@]:0:${#ARGS[@]}-1}")

FUNC_NAME="PLACEHOLDER_FUNC"

NM_OUTPUT=$("$NM_CMD" --numeric-sort "$OBJ_FILE" 2>/dev/null | grep " T ")
if [ -z "$NM_OUTPUT" ]; then
    exec "$OBJDUMP_CMD" "${OBJDUMP_ARGS[@]}" "$OBJ_FILE"
fi

FUNC_LINE=$(echo "$NM_OUTPUT" | grep " T $FUNC_NAME$")
if [ -z "$FUNC_LINE" ]; then
    exec "$OBJDUMP_CMD" "${OBJDUMP_ARGS[@]}" "$OBJ_FILE"
fi

START_ADDR=$(echo "$FUNC_LINE" | awk '{print $1}')
NEXT_ADDR=$(echo "$NM_OUTPUT" | awk -v addr="$START_ADDR" '
  found && $1 != addr { print $1; exit }
  $1 == addr { found = 1 }
')

if [ -n "$NEXT_ADDR" ]; then
    exec "$OBJDUMP_CMD" "${OBJDUMP_ARGS[@]}" --start-address="0x$START_ADDR" --stop-address="0x$NEXT_ADDR" "$OBJ_FILE"
else
    exec "$OBJDUMP_CMD" "${OBJDUMP_ARGS[@]}" --start-address="0x$START_ADDR" "$OBJ_FILE"
fi
OBJEOF
sed -i '' "s/PLACEHOLDER_FUNC/$FUNC_NAME/" "$WORKDIR/objdump_wrapper.sh"
chmod +x "$WORKDIR/objdump_wrapper.sh"

# 6. Write compile.sh
cat > "$WORKDIR/compile.sh" << COMPEOF
#!/bin/bash
set -e
CFILE="\$(realpath "\$1")"
OBJFILE="\$(realpath "\$3")"
TMPDIR="\$(mktemp -d)"

# Strip block comments (agbcc doesn't support them)
perl -0777 -pe 's|/\*.*?\*/||gs' "\$CFILE" > "\$TMPDIR/stripped.c"

# Preprocess
cpp -P "\$TMPDIR/stripped.c" "\$TMPDIR/preprocessed.c"

# Compile
cd "$PROJ"
ASM_DIR="\$(dirname "\$OBJFILE")"
ASM_FILE="\$ASM_DIR/$FUNC_NAME.s"

"./tools/agbcc/bin/agbcc" \\
    "\$TMPDIR/preprocessed.c" -o "\$ASM_FILE" \\
    -mthumb-interwork -Wimplicit \\
    -Wparentheses -Werror -O2 -g -fhex-asm

sed -i '' '/\.size/d' "\$ASM_FILE"

arm-none-eabi-as -mcpu=arm7tdmi -mthumb-interwork "\$ASM_FILE" -o "\$OBJFILE"

rm -rf "\$TMPDIR"
COMPEOF
chmod +x "$WORKDIR/compile.sh"

# 7. Run permuter
echo "Running decomp-permuter for $FUNC_NAME..."
echo "  Base: $BASE_C"
echo "  Target: $TARGET_OBJ"
echo "  Flags: ${PERMUTER_FLAGS[*]:-none}"
echo ""

exec "$PERMUTER_PYTHON" "$PERMUTER_DIR/permuter.py" "${PERMUTER_FLAGS[@]}" "$WORKDIR"
