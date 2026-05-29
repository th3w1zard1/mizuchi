# Toolchain Prerequisites

`[REPO]` Checklist for matching decompilation in this workspace. `[OFFICIAL]` Tool roles from Mizuchi / Macabeus workflow.

## Required for verification

| Tool | Purpose | Install hint |
|------|---------|--------------|
| **objdiff** | 0 diff = perfect match | [simonlindholm/objdiff](https://github.com/simonlindholm/objdiff) |
| **Project compiler** | Rebuild candidate `.o` with same flags as game | MSVC / clang / agbcc per project |
| **jq** | Match-claim hook (`.cursor/hooks.json`) | `dnf install jq` / `apt install jq` |
| **ruby** (or PyYAML) | `scripts/validate-prompt-settings.sh` | Ruby stdlib YAML preferred; else `pip install pyyaml` |

## Ghidra exploration

| Tool | Purpose |
|------|---------|
| **AgentDecompile MCP** (`agdec-http`) | Decompile, xrefs, types, cross-build match |
| **Ghidra shared server** | Odyssey programs at `170.9.241.140:13100/Odyssey` when configured |

## Programmatic phase

| Tool | Purpose |
|------|---------|
| **m2c** | Asm → C seed |
| **decomp-permuter** | Brute-force C mutations |
| **m2ctx** (or project equivalent) | Context headers for compile |

## Full pipeline (optional)

| Tool | Purpose |
|------|---------|
| **[Mizuchi](https://github.com/macabeus/mizuchi)** | Daemon: setup → programmatic → AI → integrator |
| **Node.js** | Run Mizuchi CLI (`npm start --`) |
| **Claude API** | `claude-runner` plugin when using Mizuchi AI phase |

## Cursor plugin

| Item | Path |
|------|------|
| Plugin | `~/.cursor/plugins/local/matching-decompilation-re/` |
| Enable | Cursor Settings → Plugins |
| Workspace config template | `mizuchi.example.yaml` |
| Prompt template | `prompts/_template/` |

## Ghidra shared-server constraints

`[REPO]` Observed on `170.9.241.140:13100/Odyssey` (2026-05-29):

| Program | Decompiler | Disassembly (`get-function`) |
|---------|------------|----------------------------|
| `/K1/k1_win_gog_swkotor.exe` | Failed to launch | Use other binaries or asm-only path |
| `/TSL/k2_win_gog_aspyr_swkotor2.exe` | Failed to launch | Same |
| `/TSL/k2_linux_swkotor2.elf` | Failed to launch | Same |
| `/TSL/k2_xbox_default.xbe` | Failed to launch | **Works** — use for xref/asm export |

When decompiler is down: export **disassembly** from `get-function`, build prompts from asm, seed m2c manually.

**MCP tip:** Always pass `program_path` as a quoted JSON string, e.g. `"/TSL/k2_xbox_default.xbe"`.

## Example prompt in this workspace

| Path | Role |
|------|------|
| `prompts/fun_00148020/` | 12-byte Xbox getter scaffolded from Ghidra asm |
| `prompts/_template/` | Copy for new functions |

## `[OPEN]` Not yet verified in this workspace

- End-to-end `mizuchi run` against a live Odyssey decomp tree
- Real `compilerScript` (MSVC/clang/agbcc) replacing `scripts/compile-placeholder.sh`
- Golden `.o` for `FUN_00148020` in `build/xbox/`
