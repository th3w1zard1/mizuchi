# Meta: Evidence & Caveats

## Source priority (this effort)

1. User instruction — RE workflow only, no benchmarks
2. Plugin `matching-decompilation-re` artifacts
3. [Research brief](https://github.com/macabeus/mizuchi) via plugin `docs/research-brief.md`
4. Mizuchi upstream README / integrator docs

## Caveat register

| ID | Caveat | Impact |
|----|--------|--------|
| C1 | Medium article paywalled; Substack mirror used | Minor wording drift possible |
| C2 | Mizuchi upstream verified via shallow clone (`prompt-settings.ts`, `claude-runner-plugin.ts`); not vendored in workspace | Run `mizuchi run` locally when integrating daemon |
| C3 | Agent cannot pre-check objdiff in Mizuchi MCP before submit | False match claims — use hook + verify skill; trust `PERFECT MATCH` only after real objdiff |
| C4 | Integrator is project-specific | `integrate()` module required per repo |
| C5 | Plugin hooks may need project `hooks.json` wiring | Guard not active until configured |
| C6 | Shared Ghidra server: decompiler process fails to launch for PC `.exe` and Linux `.elf`; Xbox `.xbe` still yields disassembly via `get-function` | Use asm + xrefs for prompts; do not wait on pseudocode |
| C7 | `search-everything` requires `program_path` as a JSON string (e.g. `"/TSL/k2_xbox_default.xbe"`) | Unquoted paths break MCP JSON |

## Maintenance

- Re-sync with Mizuchi README on version bumps
- Update `research-brief.md` when article or Mizuchi docs change
- Add matched examples to Decomp Atlas after integrates

## Validation performed

- [SYNTH] Reference docs created to satisfy skill cross-links (2026-05-29)
- [REPO] Mizuchi `settings.yaml` / `compile_and_view_assembly` contract verified from upstream source (2026-05-29)
- [REPO] Example prompt scaffold: `prompts/fun_00148020/` from Xbox xbe disassembly (2026-05-29)
- [REPO] `compilerScript` wired to `scripts/compile-placeholder.sh` (still exits 1 until real compiler)
- [OPEN] End-to-end Mizuchi run not executed in this workspace
- [OPEN] Golden `build/xbox/fun_00148020.o` not present in this workspace
