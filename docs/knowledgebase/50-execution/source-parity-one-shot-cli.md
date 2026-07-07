# Source Parity One-Shot CLI

Updated: 2026-06-29

This is the imperative/resumable entrypoint for turning the current SWKOTOR
source-parity workflow into a single command. It does not claim impossible
whole-executable semantic recovery. It automates the current proof-producing
lanes and keeps the full goal explicit.

## Quickstart

Run from the ReconstructKit workspace:

```bash
./scripts/decomp-cli.sh source-parity-one-shot \
  /run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor \
  --resume
```

The input may be either a game folder or a direct executable path. For a folder,
the command prefers `swkotor.exe` and falls back to the largest `.exe`.

## What It Automates

The `swkotor` profile currently runs these stages:

1. `discover`: locate the binary and record SHA256.
2. `prepare`: copy the target into `target/swkotor-unpack/` and unpack with
   Steamless when needed.
3. `inventory`: reuse or regenerate the Ghidra function inventory.
4. `match-trivial`: run conservative high-level C recognizers.
5. `match-reloc`: run relocation-aware wrapper/thunk recognizers.
6. `export-source`: export objdiff-verified candidates to source shards.
7. `compile-source`: compile the recovered shard with the configured MSVC/Wine
   toolchain.
8. `derive-coverage`: regenerate `coverage.json` and `inventory-summary.json`
   from authoritative summaries.
9. `queue`: regenerate the prioritized next-function queue.
10. `index-examples`: build matched-example features, nearest-example retrieval,
    and a strategy-class summary for queued functions.
11. `profile-corpus`: select a diverse verified-example corpus and sweep
    available compiler/flag profiles against it.
12. `synthesize-candidates`: generate bounded C candidates from binary-derived
    byte patterns for queued functions, compile them, and accept only objdiff
    zero.

Coverage is derived. Do not manually edit `target/swkotor-recovered/coverage.json`
or `target/swkotor-unpack/facts/inventory-summary.json`.

## Resume And Cancellation

The command writes:

- `target/source-parity-one-shot/swkotor/state.json`
- `target/source-parity-one-shot/swkotor/events.jsonl`
- `target/source-parity-one-shot/swkotor/report.json`
- `target/source-parity-index/swkotor/summary.json`
- `target/source-parity-index/swkotor/strategy.json`
- `target/source-parity-profile/swkotor/summary.json`
- `target/source-parity-synthesis/swkotor/summary.json`
- `target/source-parity-synthesis/swkotor/attempts.jsonl`
- `target/source-parity-synthesis/swkotor/accepted.jsonl`

Press `Ctrl-C` to cancel. The runner marks the stage as cancelled and leaves
completed receipts in place. Re-run with the same command to continue; use
`--force` to intentionally rerun every selected stage.

Useful bounded runs:

```bash
./scripts/decomp-cli.sh source-parity-one-shot <folder> --stop-after inventory
./scripts/decomp-cli.sh source-parity-one-shot <folder> --stop-after derive-coverage
./scripts/decomp-cli.sh source-parity-one-shot <folder> --stop-after synthesize-candidates --synthesis-limit 5
./scripts/decomp-cli.sh source-parity-one-shot <folder> --json
```

Run the synthesis lane directly:

```bash
./scripts/decomp-cli.sh source-parity-synthesize --limit 10
./scripts/decomp-cli.sh source-parity-synthesize --limit 10 --dry-run
```

## Important Options

| Option | Purpose |
| --- | --- |
| `--trivial-limit N` | Maximum trivial candidates to attempt. |
| `--reloc-limit N` | Maximum relocation-wrapper candidates to attempt. |
| `--queue-limit N` | Number of next functions to emit. |
| `--index-limit N` | Number of queued functions to feature-index for retrieval/strategy. |
| `--retrieval-top-k N` | Number of matched examples to retrieve per queued function. |
| `--profile-max-cases N` | Number of verified examples to include in the compiler-profile corpus. |
| `--profile-timeout SECONDS` | Timeout for the compiler-profile corpus sweep. |
| `--profile-select-only` | Select corpus cases without running the expensive compiler sweep. |
| `--synthesis-limit N` | Number of queued functions to inspect for automatic source-candidate generation. |
| `--synthesis-max-variants-per-function N` | Maximum generated source variants per inspected function. |
| `--synthesis-max-attempts-per-function N` | Maximum candidate attempts per inspected function. `0` uses source-parity-synthesize’s `--max-variants-per-function` fallback. |
| `--synthesis-strategies LIST` | Optional comma-separated strategy/tag filter for synthesis. |
| `--synthesis-timeout SECONDS` | Timeout per synthesis compile/objdiff subprocess. |
| `--synthesis-dry-run` | Emit generated candidates without compile/objdiff. |
| `--progress-every N` | Print worker progress every N match/compile attempts during long stages. |
| `--refresh-inventory` | Force Ghidra inventory regeneration. |
| `--vc-root PATH` | MSVC toolchain root. |
| `--wineprefix PATH` | Wine prefix for MSVC. |
| `--steamless-cli PATH` | Steamless CLI path for packed SWKOTOR executables. |
| `--ghidra PATH` | Ghidra `analyzeHeadless` path. |
| `--stage-timeout SECONDS` | Default subprocess timeout. |
| `--no-compile` | Export source but skip shard compilation. |
| `--json` | Emit progress as JSONL events. |

## Claim Boundary

Accepted source still means high-level C/C++ compiled through the selected
compiler profile and accepted by objdiff zero for its target function slice.
The current CLI automates partial function-source recovery and the next queue.
It does not yet solve full executable source parity, translation-unit
reconstruction, data layout, libraries, or linker order.

The synthesis lane is intentionally automatic: it emits candidates from
instruction bytes, queue metadata, feature classes, and retrieved-example
context. Manually written scratch C/C++ is not an input to the lane. Any
non-matching generated candidate remains negative evidence in `attempts.jsonl`;
it is not promoted to recovered source.

## Next Architecture Work

To move toward the requested first-try source-parity goal, the CLI should grow
new stages rather than ad hoc manual edits:

1. Broader source-shape matrix search for nontrivial mismatches.
2. Type/vtable/global recovery from Ghidra/AgentDecompile.
3. AI candidate generation constrained by the retrieved examples and current
   diff cluster.
4. Translation-unit assembly and linker-layout modeling.
