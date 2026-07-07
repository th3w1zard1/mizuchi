**Recovery Orchestrator**

*Updated: 2026-06-29*

The recovery runtime package now also exposes a neutral `recovery_runtime` namespace with a compatibility implementation kept in `src/reconkit_re`; together they replace the earlier collection of ad hoc scripts for source parity recovery. The system is a single, stateful CLI path that processes folders or binaries, supports resumable progress, and only advances to source claims with explicit verification.

### Command-Line Interface

The main commands are:

```bash
./scripts/decomp-cli.sh inspect <folder-or-binary>
./scripts/decomp-cli.sh recover <folder-or-binary> --resume
./scripts/decomp-cli.sh recover <folder-or-binary> --resume
```

**Useful bounded (limited-scope) runs:**

```bash
./scripts/decomp-cli.sh recover <target> --stop-after plan-strategy
./scripts/decomp-cli.sh recover <target> --function-analysis agentdecompile
./scripts/decomp-cli.sh recover <target> --function-analysis objdump
./scripts/decomp-cli.sh recover <target> --function-facts-jsonl target/<app>/function-facts.jsonl
./scripts/decomp-cli.sh recover <target> --byte-authority --stage-timeout 300
./scripts/decomp-cli.sh recover <target> --snapshot-existing-recovery rev1
./scripts/decomp-cli.sh recover <target> --resume --json
```

### Current Stages

The recovery process proceeds through the following stages:

1. **Discover**: Identify the target binary from a file or folder, compute its hash, and infer basic format and architecture information.

2. **Inspect Capabilities**: Catalog available local analysis tools and reusable repository resources (such as `objdiff`, `clang`, `wine`, and package generators).

3. **Inventory Binary**: Extract structural details from the binary, including PE/ELF sections, imports or dynamic symbols, executable ranges, readable/writable data ranges, entry point, and symbol counts. This is done without target-specific assumptions.

4. **Discover Functions**: Identify potential function boundaries using defined symbols, entry points, and broad executable ranges. These serve as initial candidates for further processing, not yet verified boundaries.

5. **Analyze Functions**: Optionally enrich function candidates using external tools. The `agentdecompile` option leverages the AgentDecompile interface to generate `function-facts.jsonl` and promote high-confidence candidates. The `objdump` option serves as a fallback for symbol or label-based candidates. All tool-provided labels remain candidates until verified through later slicing and validation stages.

6. **Generate Source Candidates**: Create recovery tasks from function candidates. When decompiler output is available from AgentDecompile, it produces machine-generated `candidate.c` files in the `source-generation/` directory. These files are unverified inputs for later compilation and comparison steps.

7. **Plan Strategy**: Determine the appropriate recovery approach and required evidence based on the binary format and available host tools.

8. **Byte Authority**: (Optional) Generate a generic, byte-exact source or emitter package for precise proof and validation purposes.

9. **Legacy Adapter**: A dedicated compatibility layer for older, target-specific scripts. It is isolated to prevent the new CLI from embedding specific behaviors (such as for SWKOTOR).

10. **Snapshot Existing Recovery**: If prior verified recovery artifacts exist for this exact binary, copy them into a labeled snapshot (e.g., `rev1`) with a hash manifest.

11. **Report**: Compile and present the overall state, events, target identity, strategy, and results from each recovery lane.

### Decision Model

Before claiming semantic source parity (i.e., that the recovered source accurately matches the original binary), the system must establish the following:

- Precise function boundaries with byte ranges and relocation information.
- Function candidates categorized by confidence level (high from defined symbols, medium from entry points, low from executable ranges requiring further refinement).
- Tool-supported candidates from `objdump` or similar analyzers, which still need verification.
- Comprehensive binary inventory (code and data ranges, imports, symbols, sections, image base, entry point, etc.).
- Compiler details (family, version, optimization level, ABI, and code-generation characteristics).
- Calling conventions, stack usage, register allocation, and related constraints.
- Structural elements such as imports, globals, vtables, data sections, and link-time layout.
- Matched and negative examples from prior `objdiff` runs.
- A defined source-generation strategy using decompilers, synthesis, models, or compiler feedback loops.

Manual C/C++ source is not accepted as direct input for the generic recovery process. If no automated candidate is available, the lane status is set to `needs-automatic-source-generation`.

**Acceptance Gate**: Verification requires `objdiff` to show zero differences, or a stricter full executable rebuild match.

### Research Grounding

Effective matching and decompilation workflows go beyond simply running a decompiler. Approaches such as Chris Lewis’ one-shot method emphasize structured loops, scoring, defensive tooling, and clear stopping conditions based on `objdiff` evidence. Macabeus’ ReconstructKit/Kappa documentation highlights the value of matched examples, call-target retrieval, rich prompt context, and exact assembly matching. Modern decompiler research similarly stresses compiler-aware techniques, control-flow representations, and constraint-guided refinement—all of which rely on structured evidence and iterative validation rather than unverified source generation.

### Claim Boundary

The current implementation provides the orchestration framework and generic byte-level authority support. It does not yet perform fully automatic semantic source recovery for arbitrary applications. That capability remains under development until the matching-decompilation lane can reliably produce and verify all necessary function, data, and linkage artifacts for the chosen target.
