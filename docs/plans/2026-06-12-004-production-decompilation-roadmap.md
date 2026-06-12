# Production decompilation roadmap

## Policy

Automated tests are removed for this buildout phase. Until Mizuchi is functional
end-to-end, verification is manual and proof-artifact driven: run the actual CLI,
inspect generated workspace files, rebuild when possible, compare bytes/objects,
and record uncertainty instead of using fixture tests as confidence theater.

## Remaining implementation

1. Stabilize the Rust `decomp` entrypoint.
   - Keep `decomp <target> --project <dir>`, `--rebuild`, `--verify`, `--match`,
     `status`, and `report` as the product surface.
   - Preserve `scripts/decomp-cli.sh` only as a compatibility shim while the Rust
     CLI owns new workspace creation and reporting.

2. Make intake truthful for arbitrary targets.
   - PE, ELF, Mach-O, archives, and firmware blobs must probe without panic.
   - Unsupported phases must emit `uncertainty.json` and `verification.json`
     entries such as `unsupported_format`, `compiler_unknown`, or
     `semantic_unknown`.
   - No source body is emitted as recovered unless it is backed by rebuild and
     comparison evidence.

3. Build the reconstruction workspace contract.
   - Required files: `case.yaml`, `analysis.json`, `reconstruction.json`,
     `build-plan.json`, `verification.json`, `uncertainty.json`, `objdiff.json`,
     `report.md`, and generated build scripts.
   - Source candidates may be imported or generated as blocked stubs, but blocked
     stubs must be marked as uncertainty and must not be promoted.

4. Implement verification expansion.
   - Generate objdiff project units from the reconstruction graph.
   - Add native byte, section, symbol, relocation, and object comparison for every
     produced candidate artifact.
   - Treat CFG/type/symbol recovery as advisory unless paired with binary or
     object proof.

5. Recover toolchain behavior.
   - Parse nearby project config and compiler scripts.
   - Record exact replayed compiler invocations separately from inferred compiler
     identity.
   - Do not claim original compiler/toolchain recovery unless the evidence proves
     it.

6. Add analysis providers behind adapters.
   - Use Ghidra headless/AgentDecompile for import, disassembly, types, CFG, and
     evidence extraction.
   - Keep Ghidra/LLM output as evidence only, never as proof.
   - Add future Binary Ninja, LLVM/object, RetDec, and native Rust parser
     integrations behind typed runners.

7. Build the recovery loop.
   - Programmatic recovery first.
   - AI/repair attempts only operate on candidates and metadata.
   - Every retry records drift, mismatch class, build logs, and uncertainty.
   - Stop on byte/object match and produce a proof bundle.

8. Package the product.
   - Keep `install.sh` and `install.ps1` lightweight.
   - Fetch release metadata, install `decomp`, check optional tools, and print
     exact remediation commands.
   - Do not silently install heavyweight RE tools.

## Manual proof checklist

For each meaningful slice, manually run:

```bash
./scripts/decomp-cli.sh verify-surface --quiet
cargo run -p decomp-cli -- <real-or-fixture-target> --project /tmp/mizuchi-manual
cargo run -p decomp-cli -- report --project /tmp/mizuchi-manual --format json
cargo run -p decomp-cli -- <real-or-fixture-target> --project /tmp/mizuchi-manual --verify
```

Then inspect:

- generated workspace files exist and are parseable;
- `uncertainty.json` truthfully records unsupported/inferred behavior;
- no recovered source file contains fabricated final logic;
- `verification.json` does not report matched without byte/object proof;
- `objdiff.json` uses reconstruction-derived paths, not fixed source layout
  assumptions.

## Known gaps

- Full arbitrary binary-to-source recovery is not implemented.
- Compiler identity recovery is evidence-led but not complete.
- Ghidra headless extraction is not yet a native typed provider.
- Binary Ninja and RetDec are not integrated.
- Full-project rebuild orchestration is still skeletal.
- Automated tests are intentionally absent until the platform can complete the
  manual proof loop above on representative targets.
