# Execution Playbook

## Quick start (Cursor)

1. Start with `./scripts/decomp-cli.sh help`
2. Initialize a case workspace with `./scripts/decomp-cli.sh bootstrap-case --prompt prompts/<case-id>/`
3. Enable plugin `matching-decompilation-re` in Cursor if you want slash-command parity
4. Open the target in Ghidra / AgentDecompile and run `/ghidra-scout`
5. Refine the case context and prompt-local artifacts
6. Run `./scripts/decomp-cli.sh decomp-function <case-id>`
7. Confirm proof, then integrate through the verified path

Every real prompt folder should contain both:

- `case.yaml` for stable case identity
- `settings.yaml` for the strict Mizuchi tool contract
- `case.yaml` should now also capture adapter selection, intake provenance, load context,
  and proof metadata explicitly

## Per-phase commands (conceptual)

| Phase | Skill / command |
|-------|-----------------|
| Explore | `ghidra-re-workflow`, `/ghidra-scout` |
| Context | `decomp-context-builder` |
| Programmatic | `decomp-programmatic-tools` |
| Orchestrate | `decomp-pipeline`, `/decomp-function` |
| Prompt | `decomp-prompt-builder`, `/decomp-prompt` |
| Verify | `decomp-verify-match` |
| Land | `decomp-integrator` |

## objdiff gate (mandatory)

```bash
# Prefer workspace wrapper (parses exit code + output heuristics)
./scripts/objdiff-gate.sh "$TARGET_O" "$CANDIDATE_O"

# Or raw objdiff
objdiff diff "$TARGET_O" "$CANDIDATE_O"
# SUCCESS: reports 0 differences
```

Save output to `prompts/<fn>/notes.md` before any "matched" status.

## Cursor-native loop (no Mizuchi daemon)

See `cursor-native-bridge.md`. Quick path:

```bash
./scripts/validate-prompt-settings.sh prompts/<fn>/
./scripts/compile-and-view-assembly.sh --prompt prompts/<fn>/ --code-file trial.c
```

Architecture source of truth:

- `docs/knowledgebase/10-architecture-runtime/universal-entrypoint-architecture.md`
- `docs/knowledgebase/10-architecture-runtime/reference-pipeline.md`
- `docs/knowledgebase/10-architecture-runtime/workspace-contract.md`
- `docs/knowledgebase/10-architecture-runtime/target-intake-contract.md`

## Stall playbook

After 3 attempts without diff improvement:

1. Re-read asm for missed branches / delay slots
2. Check context types (struct offsets)
3. Run permuter longer
4. Swap model / extend timeout
5. Mark `blocked` + human review

## Mizuchi CLI (when installed)

```bash
mizuchi index-codebase
mizuchi run --config mizuchi.yaml
# or target a single prompt folder under promptsDir
```

## Reference paths (plugin)

- `matching-decompilation-re/docs/reference/pipeline-phases.md`
- `matching-decompilation-re/docs/reference/prompt-layout.md`
- `matching-decompilation-re/docs/reference/prompt-sections.md`
- `matching-decompilation-re/docs/research-brief.md`
