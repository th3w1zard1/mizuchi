# Mizuchi workspace — agent guide

Matching decompilation for reverse-engineered binaries (KOTOR / Odyssey focus). Success = **objdiff 0 differences** between target `.o` and recompiled C.

## Enable the plugin

1. Cursor → **Settings → Plugins** → enable `matching-decompilation-re`
2. Plugin path: `~/.cursor/plugins/local/matching-decompilation-re/`
3. Project hooks: `.cursor/hooks.json` (match-claim guard on agent stop)

## Slash commands

| Command | Purpose |
|---------|---------|
| `/ghidra-scout` | Find function in Ghidra; export asm + types |
| `/decomp-prompt` | Create `prompts/<fn>/prompt.md` + `settings.yaml` |
| `/decomp-atlas` | Index decomp codebase; find similar matched examples |
| `/decomp-function` | Run full pipeline (programmatic → AI) |
| `/decomp-integrate` | Land verified match into source tree |

Local command specs live in `.cursor/commands/`.
CLI mirror for shell execution: `./scripts/decomp-cli.sh`.

## Skills (plugin)

Load via `@matching-decompilation-overview` or by name:

- `ghidra-re-workflow` — AgentDecompile MCP exploration
- `decomp-context-builder` — m2ctx / Get Context
- `decomp-programmatic-tools` — m2c, compile, objdiff, permuter
- `decomp-pipeline` — Mizuchi phase orchestration
- `decomp-prompt-builder` — Decomp Atlas prompts
- `decomp-atlas-index` — index codebase, similar examples
- `decomp-verify-match` — objdiff gate before integrate
- `decomp-integrator` — post-match landing
- `decomp-workflow-checklist` — end-to-end per-function checklist

Workspace skill stubs and quick references: `.cursor/skills/`.

## Agents

- `ghidra-binary-scout` — binary discovery (`.cursor/agents/`)
- `decomp-prompt-architect` — prompt folder assembly
- `decomp-function-agent` — sandboxed match loop

Plugin reference: `docs/reference/agent-pitfalls.md` in the matching-decompilation-re plugin.

## Ghidra / AgentDecompile

- Programs: e.g. `/K1/k1_win_gog_swkotor.exe`, `/TSL/k2_win_gog_aspyr_swkotor2.exe`
- Shared server: `170.9.241.140:13100/Odyssey` (when configured)
- Local project: `agentdecompile_projects/my_project`

Use MCP `agdec-http` tools; see plugin `docs/reference/mcp-tools.md`.
Workspace MCP wiring template: `.cursor/mcp.json`.

## Mizuchi upstream

When a full decomp project uses [macabeus/mizuchi](https://github.com/macabeus/mizuchi):

- Config: `mizuchi.example.yaml` in this workspace (copy to `mizuchi.yaml` in decomp project)
- Prompt folders: `prompts/<name>/` with `prompt.md` + `settings.yaml` (three fields only)
- Example scaffold: `prompts/fun_00148020/` (Xbox `.xbe`, asm-only from Ghidra)
- Validate prompt folder: `./scripts/validate-prompt-settings.sh prompts/<name>/`
- Run: `npm start -- run --config mizuchi.yaml`
- AI tool (Mizuchi MCP): `compile_and_view_assembly({ code, function_name })`
- AI tool (Cursor-native): `./scripts/compile-and-view-assembly.sh --prompt prompts/<name>/ --code-file trial.c`
- Verify match: `./scripts/objdiff-gate.sh <target.o> prompts/<name>/build/candidate.o`
- Programmatic phase: `./scripts/run-programmatic-phase.sh --prompt prompts/<name>/`
- Bridge doc: `docs/knowledgebase/50-execution/cursor-native-bridge.md`

## Knowledgebase

| Layer | Path |
|-------|------|
| Intent | `docs/knowledgebase/00-intent/` |
| Architecture | `docs/knowledgebase/10-architecture-runtime/` |
| Theory | `docs/knowledgebase/20-domain-theory/` |
| Playbook | `docs/knowledgebase/50-execution/` |
| Caveats | `docs/knowledgebase/90-meta/` |

## Invariants

1. Never claim match without objdiff 0
2. Programmatic phase before AI; stop on perfect match
3. No direct source edits during AI matching loop
4. Ghidra decomp is exploration only

## Research source

[The Unexpected Effectiveness of One-Shot Decompilation with Claude](https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/) — one-shot workflow framing and lessons learned.

[Can LLMs Really Do Matching Decompilation?](https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288) — workflow packaged here; benchmark scoring excluded.
