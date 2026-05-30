# Mizuchi workspace

Cursor workspace for **matching decompilation** on reverse-engineered game binaries (KOTOR / Odyssey). The goal is C that recompiles to **byte-identical** object code — verified with **objdiff 0 differences**, not Ghidra pseudocode alone.

## Quick start

1. Enable plugin **matching-decompilation-re** in Cursor → Settings → Plugins  
   Path: `~/.cursor/plugins/local/matching-decompilation-re/`
2. Read `AGENTS.md` for commands, skills, Ghidra MCP, and invariants.
3. Check local command specs in `.cursor/commands/` and MCP config in `.cursor/mcp.json`.
4. Use `./scripts/decomp-cli.sh verify-surface` to assert subagents/hooks/rules/skills/commands/MCP/CLI surfaces.
5. For a new function:
   - `./scripts/bootstrap-re-pipeline.sh --prompt prompts/<fn>/` — initialize required prompt files
   - `/ghidra-scout` — find function, export asm + types
   - `/decomp-prompt` — scaffold or refine `prompts/<fn>/` (or copy `prompts/_template/` manually)
   - `/decomp-function` — programmatic → AI matching loop
   - `/decomp-integrate` — after objdiff 0, land C in the project

## Knowledgebase

Layered docs under `docs/knowledgebase/`:

| Layer | Topic |
|-------|--------|
| `00-intent` | Goals and non-goals |
| `10-architecture-runtime` | Ghidra ↔ Mizuchi bridge |
| `20-domain-theory` | Matching decompilation concepts |
| `50-execution` | Step-by-step playbook |
| `90-meta` | Evidence caveats |

Plugin reference: `~/.cursor/plugins/local/matching-decompilation-re/docs/`

## Upstream Mizuchi

Full daemon pipeline: [github.com/macabeus/mizuchi](https://github.com/macabeus/mizuchi)

- `mizuchi.example.yaml` at workspace root (copy to `mizuchi.yaml` in your decomp project)
- Prompt folders: `prompts/<name>/` with `prompt.md` + `settings.yaml` (`functionName`, `targetObjectPath`, `asm`)
- Example scaffold: `prompts/fun_00148020/` (12-byte Xbox getter from Ghidra asm)
- AI verification tool: `compile_and_view_assembly`

## Research

Workflow based on [Can LLMs Really Do Matching Decompilation?](https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288) (benchmark sections excluded from this packaging).
