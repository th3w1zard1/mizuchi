# Meta: Evidence & Caveats

## Source priority (this effort)

1. User instruction — RE workflow only, no benchmarks
2. Plugin `matching-decompilation-re` artifacts
3. [Chris Lewis one-shot decompilation article](https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/)
4. [Macabeus matching decompilation article](https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288)
5. Microsoft compiler and PE/COFF documentation for toolchain and image-format facts
6. [Research brief](https://github.com/macabeus/reconkit) via plugin `docs/research-brief.md`
7. ReconstructKit upstream README / integrator docs

## Caveat register

| ID | Caveat | Impact |
|----|--------|--------|
| C1 | Medium article content was retrieved through Firecrawl on 2026-06-29; direct browser rendering may vary | Re-check the source if exact article wording matters |
| C2 | ReconstructKit upstream verified via shallow clone (`prompt-settings.ts`, `claude-runner-plugin.ts`); not vendored in workspace | Run `reconkit run` locally when integrating daemon |
| C3 | Agent cannot pre-check objdiff in ReconstructKit MCP before submit | False match claims — use hook + verify skill; trust `PERFECT MATCH` only after real objdiff |
| C4 | Integrator is project-specific | `integrate()` module required per repo |
| C5 | Plugin hooks may need project `hooks.json` wiring | Guard not active until configured |
| C6 | External decompiler services are disabled in this workspace | Use target assembly/object-slice metadata for prompts; do not wait on pseudocode |
| C7 | Prompt input must identify the target binary and address range explicitly | Ambiguous binary provenance breaks objdiff evidence |
| C8 | Whole-file hash or byte-emitter success can be mistaken for source parity | Treat byte sources as acquisition fixtures only; require objdiff 0 on compiled C/C++ code |
| C9 | `swkotor.exe` is SteamStub-packed; Steamless produced an unpacked analysis image with `.textV` game code, but this is still an analysis target rather than a full source rebuild | Use unpacked `.textV` for function matching; do not claim whole-app parity until all executable ranges are source-built and code-diffed |
| C10 | Current `swkotor` compiler profile is underconstrained for nontrivial functions; VC7.1/VC8 flag sweeps on two harder cases did not produce perfect matches | Expand compiler-profile probes and source-shape hypotheses before expecting AI to match complex functions |

## Maintenance

- Re-sync with ReconstructKit README on version bumps
- Update `research-brief.md` when article or ReconstructKit docs change
- Add matched examples to Decomp Atlas after integrates
- Re-run compiler-profile corpus whenever a new toolchain or flag family is added

## Validation performed

- [SYNTH] Reference docs created to satisfy skill cross-links (2026-05-29)
- [REPO] ReconstructKit `settings.yaml` / `compile_and_view_assembly` contract verified from upstream source (2026-05-29)
- [REPO] Example prompt scaffold: `prompts/fun_00148020/` from Xbox xbe disassembly (2026-05-29)
- [REPO] `compilerScript` wired to `scripts/compile-placeholder.sh` (still exits 1 until real compiler)
- [OPEN] End-to-end ReconstructKit run not executed in this workspace
- [OPEN] Golden `build/xbox/fun_00148020.o` not present in this workspace
