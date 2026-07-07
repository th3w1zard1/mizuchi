# Execution Playbook

## Quick start (Cursor)

1. Enable plugin `matching-decompilation-re` in Cursor
2. Acquire target assembly/object-slice metadata from the binary inventory
3. `/decomp-prompt` — create `prompts/<fn>/`
4. `/decomp-function` — run pipeline (programmatic → AI)
5. Inspect `prompts/<fn>/build/decomp-function.json` for the command-level receipt
6. Confirm objdiff 0 via `decomp-verify-match`
7. Integrate with `/decomp-integrate`

If the compiler family, flags, or real executed code are unknown, pause before
step 4 and run the compiler-forensics workflow in
`../20-domain-theory/source-parity-strategy.md`,
`../20-domain-theory/source-parity-field-guide.md` and
`../20-domain-theory/source-parity-compiler-forensics.md`. Matching C before
that profile exists is usually wasted effort.

The required order for a new executable is:

1. Acquire the real executed code, not a packed loader view.
2. Build an executable range and function inventory.
3. Create a compiler-profile corpus from small representative target slices.
4. Sweep toolchains and flags; keep both positive and negative evidence.
5. Only then run function-level programmatic/AI matching loops.
6. Promote function slices to translation units and finally to a linked image.

For current `swkotor.exe` function-slice forensics, run the local compiler
matrix before claiming a compiler/flag hypothesis:

```bash
./scripts/swkotor-compiler-profile.sh --case FUN_0086d201 --case FUN_0086d266
column -t -s $'\t' target/swkotor-compiler-profile/summary.tsv
```

The profiler records `cl.exe` banners, exact flags, compiled objects, and
objdiff reports under `target/swkotor-compiler-profile/`. A non-100% score is
evidence for the next source-shape change, not a match.

For the Steam `swkotor.exe`, unpack the SteamStub layer before matching. The
packed `.text` section is not original compiler output.

```bash
cp /run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor/swkotor.exe \
  target/swkotor-unpack/swkotor.original.exe

mono target/steamless-release/extracted/Steamless.CLI.exe \
  --quiet --keepbind --dumppayload --dumpdrmp \
  target/swkotor-unpack/swkotor.original.exe
```

Create a target object for a selected unpacked function:

```bash
./scripts/swkotor-inventory-slice.py \
  --function FUN_00401590 \
  --symbol '_FUN_00401590@16' \
  --out-dir target/swkotor-match/FUN_00401590
```

Run the current simple high-level-C matcher over the unpacked inventory:

```bash
./scripts/swkotor-match-trivial.py --limit 200
jq '{attempted,matched,mismatched,byKind}' \
  target/swkotor-trivial-matches/summary.json
```

As of the current evidence pass, this lane verifies `256` simple `.textV`
functions with objdiff `0`. That is useful coverage growth, not whole-program
parity.

Run the relocation-aware wrapper matcher for direct `call` / `jmp` wrappers:

```bash
./scripts/swkotor-match-reloc-wrappers.py --limit 200
jq '{attempted,matched,mismatched,byKind}' \
  target/swkotor-reloc-wrapper-matches/summary.json
```

As of the current evidence pass, this lane verifies `318` additional wrapper /
thunk functions with objdiff `0`.

Export those verified candidates into a partial recovered-source shard and
compile the split source tree:

```bash
./scripts/swkotor-export-matched-source.py

VC_ROOT=/run/media/brunner56/MyBook/ReconstructKitSource/toolchains/msvc8.0-main \
WINEPREFIX=$PWD/target/toolchain-acquire/vctoolkit2003/wineprefix \
CL_OPT=/O2 \
  ./scripts/swkotor-compile-recovered-shard.py
```

The shard currently contains `574` verified functions and compiles as `574`
split C files with MSVC. It is a partial source artifact for matched functions,
not a full executable rebuild.

## Per-phase commands (conceptual)

| Phase | Skill / command |
|-------|-----------------|
| Context | `decomp-context-builder` |
| Programmatic | `decomp-programmatic-tools` |
| Orchestrate | `decomp-pipeline`, `/decomp-function` |
| Prompt | `decomp-prompt-builder`, `/decomp-prompt` |
| Verify | `decomp-verify-match` |
| Land | `decomp-integrator` |

## objdiff gate (mandatory)

```bash
# Prefer workspace wrapper (uses the shared normalized objdiff parser)
./scripts/objdiff-gate.sh "$TARGET_O" "$CANDIDATE_O"

# Machine-readable form for agents/tools
./scripts/lib/verify-objdiff.sh "$TARGET_O" "$CANDIDATE_O" --out prompts/<fn>/build/verify.json
# SUCCESS: status=matched and differences=0
```

Save output to `prompts/<fn>/notes.md` before any "matched" status.

## Integration gate

```bash
./scripts/integrate-verified-match.sh --prompt prompts/<fn> --source-out path/to/source.c
```

The wrapper re-runs `build-and-verify.sh`, copies the candidate only after a
matched verifier report, updates `case.yaml` to `integrated`, and writes
`prompts/<fn>/build/integration-receipt.json`.

## Orchestration receipt

```bash
./scripts/decomp-cli.sh decomp-function <fn>
jq . prompts/<fn>/build/decomp-function.json
```

The receipt schema is `reconkit.decomp-function.v1`. It records the terminal
phase (`programmatic` or `ai`), exit code, top-level status, and links to the
programmatic and AI phase reports when those phases ran. A blocked prompt must
stop before AI and record `status: "blocked"`.

## Cursor-native loop (no ReconstructKit daemon)

See `cursor-native-bridge.md`. Quick path:

```bash
./scripts/validate-prompt-settings.sh prompts/<fn>/
./scripts/compile-and-view-assembly.sh --prompt prompts/<fn>/ --code-file trial.c
```

## Stall playbook

After 3 attempts without diff improvement:

1. Re-read asm for missed branches / delay slots
2. Check context types (struct offsets)
3. Check compiler-profile fit: prologue, epilogue, zero-init, inc/dec, calling convention
4. Run permuter longer only if the candidate is already close
5. Swap model / extend timeout
6. Mark `blocked` + human review

## ReconstructKit CLI (when installed)

```bash
reconkit index-codebase
reconkit run --config reconkit.yaml
# or target a single prompt folder under promptsDir
```

## Reference paths (plugin)

- `matching-decompilation-re/docs/reference/pipeline-phases.md`
- `matching-decompilation-re/docs/reference/prompt-layout.md`
- `matching-decompilation-re/docs/reference/prompt-sections.md`
- `matching-decompilation-re/docs/research-brief.md`
