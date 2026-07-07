# Source Parity Implementation Roadmap

Updated: 2026-06-29

This is the practical implementation roadmap for turning a compiled C/C++
binary such as `swkotor.exe` into a source tree that can be defended as source
parity. It separates what existing tools are good at, what ReconstructKit already has,
and what still has to be built.

## The Reference Methodology

The Chris Lewis one-shot workflow is not "decompile the whole binary in one
shot." It is a throughput layer on top of a normal matching-decompilation
project:

1. Rank candidate functions by likely difficulty.
2. Pick one narrow target function.
3. Assemble only the context needed for that function.
4. Let the model propose high-level source.
5. Compile with the project compiler and flags.
6. Diff against the target object.
7. Keep only verified matches and move hard functions aside.

The Macabeus/ReconstructKit model is the same in principle. Programmatic tools,
permuters, and LLMs all feed a compile-and-diff loop. None of them are the
acceptance gate.

## Tool Roles

### AgentDecompile / Ghidra

Use these for:

- function discovery,
- decompiler output,
- call graphs and xrefs,
- imports/globals,
- type and structure hypotheses,
- binary navigation.

Do not use them as proof. They explain semantics and produce candidate inputs.
They do not identify the original compiler, preserve exact source shape, or
prove object parity.

### objdiff / target-object comparison

Use this as the acceptance gate for matching work. A candidate source is only a
match when the rebuilt object matches the target object, including code and
relevant relocations.

### Compiler-profile corpus

This is the search-space reducer. Before trying to solve large functions, build
evidence about compiler family, version, flags, frame-pointer behavior, stack
cookies, calling conventions, inlining, and runtime-library model.

### One-shot generation

Use one-shot generation only after the target function is well formed:

- reliable byte range,
- plausible object/relocation model,
- current compiler-profile hypothesis,
- known callees/imports/globals where relevant,
- matched examples from the same codegen regime.

If those are missing, one-shot is premature. The real work is still acquisition,
compiler forensics, or type/global recovery.

## What ReconstructKit Already Has

ReconstructKit already has the skeleton of the right architecture (compatibility namespace in `src/reconkit_re`; neutral `src/recovery_runtime` façade):

- `cli.py` provides a real recovery orchestrator.
- `agentdecompile.py` treats AgentDecompile as an acquisition
  layer rather than a proof surface.
- `windows.py` can process large binaries in deterministic
  function windows and assemble a recovered-source package.
- `package_sweep.py` can synthesize source-shape variants and
  sweep compiler profiles.
- `package_verify.py` can compile generated sources and compare
  candidate `.text` against packaged target slices.

That is real progress, but it is still below source parity.

## The Current Gaps

### 1. Verification is still weaker than true target-object parity

`package_verify.py` currently compares candidate object code against packaged
target slices. The file itself says this is weaker than objdiff because the full
relocation symbols and compiler/linker context are unavailable.

Implication: code-slice matches are useful evidence, but they are not the final
acceptance gate for semantic source recovery.

### 2. Compiler forensics is not yet a first-class persistent system

The repo has compiler-profile docs and scripts, but the main matching lane is
still too willing to operate before the compiler-profile corpus is mature.

Implication: nontrivial mismatches will keep looking like source problems even
when the real blocker is compiler, flag, or ABI drift.

### 3. AgentDecompile output is richer than the current downstream use

The adapter imports binaries, lists functions, and captures decompiler output,
but the later stages still underuse structured facts such as memory-access
shape, call-target clusters, and type/global hypotheses.

Implication: candidate generation is still too text-heavy and not yet
compiler-aware enough.

### 4. Translation-unit and link parity are mostly future work

The current pipeline is strongest at isolated function work. Whole-object,
whole-translation-unit, and link-order reconstruction remain open.

Implication: even many verified function matches do not yet prove executable
parity for the full application.

## The Best Feasible Implementation Path

### Phase 1. Make the verifier honest and strong

Implemented in `package_verify.py` (under the compatibility implementation at `src/reconkit_re`, mirrored in `src/recovery_runtime`): each package and per-function
result carries `verificationTier` (`generated` → `object-compilable` →
`code-slice` → `relocation-aware-code-slice` → `target-object-objdiff`) and
`acceptanceGate` (`objdiff-zero` only at the top tier). Weaker tiers are labeled
evidence, not final match claims.

1. Keep code-slice comparison, but label it explicitly as a pre-objdiff gate.
2. Build relocation-aware target objects for matched slices wherever possible.
3. Promote the default acceptance gate from code-slice parity to target-object
   `objdiff 0`.
4. Treat any weaker comparison as evidence, never as a final match claim.

### Phase 2. Build a real compiler-profile lab

1. Create probe classes: accessor, wrapper, stack-frame, local-init, loop,
   switch, virtual call, constructor, destructor, floating point, CRT wrapper.
2. Run each probe across candidate toolchains and flag profiles.
3. Persist exact environment and output:
   - compiler banner,
   - toolchain root,
   - include/lib roots,
   - effective command line,
   - source hash,
   - object hash,
   - disassembly,
   - relocation table,
   - objdiff result,
   - mismatch class.
4. Promote a compiler profile only when multiple probe classes agree.

### Phase 3. Upgrade candidate generation from decompiler text to source-shape search

1. Normalize AgentDecompile output into structured facts:
   - prototype hypothesis,
   - stack/local accesses,
   - globals/imports,
   - call targets,
   - loop/switch hints,
   - C++ object-model hints.
2. Retrieve matched examples by codegen shape, not just name or free text.
3. Generate multiple source-shape families automatically:
   - direct loads/stores vs temporaries,
   - pointer arithmetic vs array syntax,
   - explicit casts vs masked arithmetic,
   - alternate loop forms,
   - helper-call wrappers vs inline expressions.
4. Use one-shot generation to add new hypotheses, not to replace the search
   process.
5. Use permuter-style local search only once the candidate is already close.

### Phase 4. Model object and translation-unit boundaries

1. Group verified functions by likely object and translation-unit boundaries.
2. Recover shared prototypes, statics, globals, vtables, and section-local data.
3. Verify full source-built objects against reconstructed target objects.
4. Record object order and library dependencies needed for later link parity.

### Phase 5. Attempt executable-code parity last

Only after function and object coverage are mature:

1. rebuild all verified objects,
2. reproduce library and link order,
3. compare executable code ranges in the rebuilt image against the unpacked
   target image,
4. scope data/resources/debug metadata separately.

## Agent-Native Implications

ReconstructKit should stay agent-native, but only in the right places:

- The agent should be able to run every recovery lane the user can run.
- Tools should stay primitive: inspect target, analyze functions, generate
  candidates, verify objects, record profiles, import matched examples.
- Prompt-defined behavior should choose which lane to use next.
- Context injection should include current compiler-profile evidence, verifier
  strength, function-class tags, and nearby matched examples.

What should not be prompt-native is the proof gate itself. Acceptance must stay
mechanical.

## Immediate Priorities

1. Make target-object parity the explicit north star for `recover-windows`.
2. Split verification results into:
   - generated candidate,
   - object-compilable,
   - code-slice match,
   - relocation-aware slice match,
   - target-object objdiff match.
3. Build and persist the compiler-profile corpus as a first-class artifact set.
4. Enrich AgentDecompile facts into structured candidate-generation inputs.
5. Keep byte-authority and replay packaging as a separate lane with separate
   wording.

## Non-Strategies

- Do not hand-write arbitrary C/C++ as the main recovery path.
- Do not treat `.incbin`, byte arrays, copied assembly, or replay packages as
  recovered source.
- Do not trust a decompiler transcript because it "looks right."
- Do not widen prompts before narrowing compiler/profile/type uncertainty.
- Do not try whole-executable source recovery before object-level proof exists.
