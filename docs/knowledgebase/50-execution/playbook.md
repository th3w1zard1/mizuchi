# Execution Playbook

## Quick start (Cursor)

1. Enable plugin `matching-decompilation-re` in Cursor
2. Open Ghidra program via AgentDecompile MCP
3. `/ghidra-scout` — find function, export asm + types
4. `/decomp-prompt` — create `prompts/<fn>/`
5. `/decomp-function` — run pipeline (programmatic → AI)
6. Confirm objdiff 0 via `decomp-verify-match`
7. Integrate manually or Mizuchi integrator

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
