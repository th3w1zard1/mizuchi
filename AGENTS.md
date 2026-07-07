# ReconstructKit workspace — agent guide

Matching decompilation for reverse-engineered binaries (KOTOR / Odyssey focus). Success = **objdiff 0 differences** between target `.o` and recompiled C.

## Enable the plugin

1. Cursor → **Settings → Plugins** → enable `matching-decompilation-re`
2. Plugin path: `~/.cursor/plugins/local/matching-decompilation-re/`
3. Project hooks: `.cursor/hooks.json` (match-claim guard on agent stop)

## Slash commands

| Command | Purpose |
|---------|---------|
| `/decomp-prompt` | Create `prompts/<fn>/prompt.md` + `settings.yaml` |
| `/decomp-atlas` | Index decomp codebase; find similar matched examples |
| `/decomp-function` | Run full pipeline (programmatic → AI) |
| `/decomp-integrate` | Land verified match into source tree |

Local command specs live in `.cursor/commands/`.
CLI mirror for shell execution: `./scripts/decomp-cli.sh`.

## Skills (plugin)

Load via `@matching-decompilation-overview` or by name:

- `decomp-context-builder` — m2ctx / Get Context
- `decomp-programmatic-tools` — m2c, compile, objdiff, permuter
- `decomp-pipeline` — ReconstructKit phase orchestration
- `decomp-prompt-builder` — Decomp Atlas prompts
- `decomp-atlas-index` — index codebase, similar examples
- `decomp-verify-match` — objdiff gate before integrate
- `decomp-integrator` — post-match landing
- `decomp-workflow-checklist` — end-to-end per-function checklist

Workspace skill stubs and quick references: `.cursor/skills/`.

## Agents

- `decomp-prompt-architect` — prompt folder assembly
- `decomp-function-agent` — sandboxed match loop

Plugin reference: `docs/reference/agent-pitfalls.md` in the matching-decompilation-re plugin.

## ReconstructKit upstream

When a full decomp project uses [macabeus/reconkit](https://github.com/macabeus/reconkit):

- Config: `reconkit.example.yaml` in this workspace (copy to `reconkit.yaml` in decomp project)
- Prompt folders: `prompts/<name>/` with `prompt.md` + `settings.yaml` (three fields only)
- Case manifests: `case.yaml` carries proof target metadata, target family, optional target/candidate sources, and compiler command
- Example scaffold: `prompts/fun_00148020/` (Xbox `.xbe`, asm-only)
- Validate prompt folder: `./scripts/validate-prompt-settings.sh prompts/<name>/`
- Validate case manifests: `./scripts/validate-case-manifests.sh prompts`
- Validate one prompt end-to-end: `./scripts/decomp-cli.sh decomp-validate <name>`
- Audit production readiness: `./scripts/decomp-cli.sh decomp-readiness <name>` or `--all`
- Run: `npm start -- run --config reconkit.yaml`
- AI tool (ReconstructKit MCP): `compile_and_view_assembly({ code, function_name })`
- One-shot matcher: `./scripts/decomp-cli.sh matcher <name> --response-file response.txt` or set `RECONKIT_MATCHER_COMMAND` for a headless runner that writes `{{responseFile}}`
- Autonomous scorer: `./scripts/decomp-cli.sh scorer --queue state/queue.json --update-queue --out state/scores.json` ranks pending prompts by deterministic asm complexity; ML hooks are metadata-only for now
- Vacuum init/orchestrator: `./scripts/decomp-cli.sh vacuum init --queue state/queue.json --prompts-dir prompts`, then `./scripts/decomp-cli.sh vacuum start --queue state/queue.json --max-functions 1 --timeout 30m`; it processes scored pending prompts with persistent logs, session state, timeout, quota backoff, `resume`, and `reset-queue --name <fn>`
- One-shot task importer: `./scripts/decomp-cli.sh import-one-shot-tasks --package target/<app>/one-shot-source --prompts-dir prompts` converts `FUNCTION_RECONSTRUCTION_TASKS.json` into prompt folders with custom byte-slice verifier commands
- One-shot task coverage: `./scripts/decomp-cli.sh one-shot-task-coverage --package target/<app>/one-shot-source --prompts-dir prompts --queue state/queue.json` reports package task import/match/integration coverage without promoting semantic claims beyond the package readiness evidence
- Verified commit helper: `./scripts/decomp-cli.sh commit-verified-match --prompt prompts/<name> --dry-run` re-runs verification and stages only explicit verified source/proof paths when not dry-run
- Autonomous queue state: `./scripts/decomp-cli.sh queue init --queue state/queue.json --prompts-dir prompts` and `queue summary|next|move|attempt`
- AI tool (Cursor-native): `./scripts/compile-and-view-assembly.sh --prompt prompts/<name>/ --code-file trial.c`
- Verify match: `./scripts/build-and-verify.sh --prompt prompts/<name>/` (uses objdiff when installed, byte compare fallback for local fixtures)
- Programmatic phase: `./scripts/run-programmatic-phase.sh --prompt prompts/<name>/`
- Local proof fixture: `./scripts/decomp-cli.sh decomp-function roundtrip_identity`
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
4. Decompiler pseudocode is not proof

## Research source

[The Unexpected Effectiveness of One-Shot Decompilation with Claude](https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/) — one-shot workflow framing and lessons learned.

[Can LLMs Really Do Matching Decompilation?](https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288) — workflow packaged here; benchmark scoring excluded.
