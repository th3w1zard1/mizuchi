# Mizuchi workspace

Mizuchi is a proof-aware reverse-engineering workspace that is evolving toward a
single-entrypoint, self-contained product for source recovery across multiple target
families. Today, the strongest implemented proof path is still matching decompilation:
candidate C must recompile to **byte-identical** object code, verified with
**objdiff 0 differences**.

## Quick start

1. Start with the primary shell surface:
   - `./scripts/decomp-cli.sh help`
   - `./scripts/decomp-cli.sh verify-surface`
2. Read `AGENTS.md` for the current runtime invariants, adapters, and agent surfaces.
3. Enable plugin **matching-decompilation-re** in Cursor if you want slash-command parity:
   - Path: `~/.cursor/plugins/local/matching-decompilation-re/`
4. For a new case:
   - `./scripts/decomp-cli.sh bootstrap-case --prompt prompts/<case-id>/`
   - `/ghidra-scout` or equivalent MCP flow for discovery
   - `./scripts/decomp-cli.sh decomp-function <case-id>` for the programmatic-first loop
   - `./scripts/decomp-cli.sh decomp-integrate <case-id> <target.o>` only after proof passes
5. Use `./scripts/decomp-cli.sh status` to inspect queued or in-progress work.

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
