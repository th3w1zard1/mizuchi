# Cursor-Native Bridge (without Mizuchi daemon)

When Mizuchi is not running, use workspace scripts to replicate the **compile → objdiff** loop and the AI sandbox tool **`compile_and_view_assembly`**.

## Scripts

| Script | Role |
|--------|------|
| `scripts/validate-prompt-settings.sh` | Validate `settings.yaml` (3 fields) + `prompt.md` |
| `scripts/run-programmatic-phase.sh` | Orchestrate get-context → m2c → compile/objdiff → permuter |
| `scripts/get-context.sh` | Run `global.getContextScript` (m2ctx) into `context/` |
| `scripts/run-m2c.sh` | m2c pass → `build/m2c.c` |
| `scripts/run-permuter.sh` | decomp-permuter → `build/permuter-best.c` |
| `scripts/compile-trial.sh` | Compile C → `build/candidate.o`; objdiff if golden `.o` exists |
| `scripts/compile-and-view-assembly.sh` | Prepend `context/ctx.h`, compile, `objdump`, objdiff summary |
| `scripts/objdiff-gate.sh` | Exit 0 only when objdiff reports **0 differences** |
| `scripts/lib/prompt-settings.sh` | Shared YAML field reader (ruby or PyYAML) |
| `scripts/lib/mizuchi-config.sh` | Read `mizuchi.yaml` templates and plugin paths |
| `scripts/lib/permuter-run.py` | Permuter workdir setup (used by `run-permuter.sh`) |

## AI matching loop (Cursor agent)

Equivalent to Mizuchi `compile_and_view_assembly`:

```bash
./scripts/compile-and-view-assembly.sh \
  --prompt prompts/fun_00148020/ \
  --code-file /tmp/trial.c
```

Or stdin:

```bash
cat /tmp/trial.c | ./scripts/compile-and-view-assembly.sh \
  --prompt prompts/fun_00148020/ --code-stdin
```

**Agent rules during loop:**

- Allowed: Read/Glob/Grep, run scripts above, write under `prompts/<fn>/` only
- Forbidden: edit matched source tree; claim match without `diff_count: 0` / objdiff gate pass

## Verification gate

```bash
./scripts/objdiff-gate.sh build/xbox/fun_00148020.o prompts/fun_00148020/build/candidate.o
```

Success = exit code **0** and output mentioning zero differences.

## Programmatic one-liner

```bash
./scripts/run-programmatic-phase.sh --prompt prompts/fun_00148020/
```

Stops when m2c or permuter output passes `objdiff-gate.sh`.

## Compiler wiring

1. Copy `mizuchi.example.yaml` → `mizuchi.yaml`
2. Replace `global.compilerScript` with your real toolchain (MSVC/clang/gcc)
3. Until then, `scripts/compile-placeholder.sh` fails by design — documents the missing bridge

`compile-trial.sh` reads `compilerScript` from `mizuchi.yaml` when present.

## Context (m2ctx)

- Default header stub: `context/ctx.h`
- Mizuchi path: `global.getContextScript` in `mizuchi.yaml`
- `compile-and-view-assembly.sh` prepends context before compile (matches Mizuchi concat behavior)

## [OPEN]

- Golden `.o` for `prompts/fun_00148020/` not in workspace — objdiff skipped until `targetObjectPath` exists
- Real `getContextScript` / m2ctx not wired — `context/ctx.h` is a stub until configured
- `vendor/m2c` and `vendor/decomp-permuter` optional — set paths in `mizuchi.yaml` or env

## Plugin parity

Skills `decomp-programmatic-tools` and `decomp-verify-match` reference these scripts. Hook `decomp-match-claim-guard.sh` still applies on agent `stop`.
