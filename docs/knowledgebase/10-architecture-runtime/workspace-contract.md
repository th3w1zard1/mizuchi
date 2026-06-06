# Workspace Contract

This document defines the stable on-disk contract for a decompilation case. The current
workspace still uses article-faithful scripts, but new automation should target this
contract rather than inventing per-script state.

## Case directory

Each case lives under:

```text
prompts/<case-id>/
```

Required files:

- `case.yaml` — stable case identity for the app/orchestrator
- `settings.yaml` — strict Mizuchi tool contract for the current workflow
- `prompt.md` — agent brief and working context
- `notes.md` — human notes, blockers, hypotheses, and commentary

## `case.yaml`

`case.yaml` is the architecture-level case manifest. It is broader than
`settings.yaml`, but intentionally small enough to stay stable as the runtime evolves.

Required shape:

```yaml
schemaVersion: 1
caseId: fun_00148020
target:
  family: odyssey
  binary: /TSL/k2_xbox_default.xbe
  platform: xbox
symbol:
  name: FUN_00148020
  locator: "0x00148020"
proof:
  targetObjectPath: build/xbox/fun_00148020.o
workspace:
  promptPath: prompts/fun_00148020
  buildDir: build
```

Field intent:

- `caseId` — stable workspace identifier; must match the prompt folder name
- `target.family` — adapter family (`odyssey`, `elf-ps2`, `pe-win32`, etc.)
- `target.binary` — binary/module identity used during discovery
- `target.platform` — platform/toolchain family for the case
- `symbol.name` — linker or analysis-facing symbol identifier
- `symbol.locator` — address, offset, or equivalent locator string
- `proof.targetObjectPath` — golden object path used by the proof gate
- `workspace.promptPath` — canonical workspace-relative case path
- `workspace.buildDir` — prompt-local artifact directory

## `settings.yaml`

`settings.yaml` stays strict because it is the current tool contract:

- `functionName`
- `targetObjectPath`
- `asm`

`case.yaml` and `settings.yaml` must agree on symbol name and golden object path.

## Derived state

Case state is **derived from artifacts**, not declared authoritatively in `notes.md`.

Allowed lifecycle labels in human-facing docs:

- `queued`
- `in_progress`
- `blocked`
- `matched`
- `integrated`

Rules:

- `matched` requires a proof artifact and a passing `objdiff` gate
- `integrated` requires a verified match plus target-tree landing
- `blocked` may be declared by notes when prerequisites are missing

Today, `scripts/validate-prompt-status.sh` enforces the strongest part of that rule:
no prompt may claim `matched` without proof artifacts.

## Run artifacts

Prompt-local runtime output belongs under:

```text
prompts/<case-id>/build/
```

Expected artifact families:

- `get-context.log`
- `compile.log`
- `candidate.o`
- `m2c.c`
- `permuter-best.c`
- assembly dumps / diff summaries
- future machine-readable run metadata (`run.json`, `verification.json`)

Not every file exists on every run. The contract is the directory boundary and the
artifact family names, not a requirement that all files always exist.

## Legacy bridge note

Some current flows still depend on workspace-global assets such as `context/ctx.h`.
That is acceptable for the article-faithful bridge, but new orchestration should treat
prompt-local artifacts and the case manifest as the primary contract.
