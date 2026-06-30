# Toolchain Prerequisites

`[REPO]` Checklist for matching decompilation in this workspace. `[OFFICIAL]` Tool roles from Mizuchi / Macabeus workflow.

## Required for verification

| Tool | Purpose | Install hint |
|------|---------|--------------|
| **objdiff** | 0 diff = perfect match | [encounter/objdiff](https://github.com/encounter/objdiff) |
| **Project compiler** | Rebuild candidate `.o` with same flags as game | MSVC / clang / agbcc per project |
| **jq** | Match-claim hook (`.cursor/hooks.json`) | `dnf install jq` / `apt install jq` |
| **ruby** (or PyYAML) | `scripts/validate-prompt-settings.sh` | Ruby stdlib YAML preferred; else `pip install pyyaml` |

Current local verifier:

| Tool | Observed |
|------|----------|
| `objdiff` | `objdiff-cli 3.7.2` |

## Local MSVC profiles for `swkotor.exe`

| Profile | Root | Banner |
|---------|------|--------|
| `vc71` | `target/toolchain-acquire/vctoolkit2003/msitools-extract/Program Files/Microsoft Visual C++ Toolkit 2003/` | `Microsoft (R) 32-bit C/C++ Optimizing Compiler Version 13.10.3052 for 80x86` |
| `vc80` | `/run/media/brunner56/MyBook/MizuchiSource/toolchains/msvc8.0-main/` | `Microsoft (R) 32-bit C/C++ Optimizing Compiler Version 14.00.50727.42 for 80x86` |

VC7.1 was acquired from `VCToolkitSetup.exe` and extracted with `msitools`.
The installer SHA256 is
`03aad135c22e953e0928b118705338afdbd08abf8e4039038ef77945504e65fa`.

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

## Example prompt in this workspace

| Path | Role |
|------|------|
| `prompts/fun_00148020/` | 12-byte Xbox getter scaffolded from target assembly |
| `prompts/_template/` | Copy for new functions |

## `[OPEN]` Not yet verified in this workspace

- End-to-end `mizuchi run` against a live Odyssey decomp tree
- Real `compilerScript` (MSVC/clang/agbcc) replacing `scripts/compile-placeholder.sh`
- Golden `.o` for `FUN_00148020` in `build/xbox/`
