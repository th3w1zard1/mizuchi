# One-Shot Decompilation Guidance

Updated: 2026-06-29

## External observations (dated 2026-05-29)

- [OFFICIAL] Chris Lewis, *The Unexpected Effectiveness of One-Shot Decompilation with Claude*:
  - https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/
- [OFFICIAL] Simon Willison mirror/summary with implementation links:
  - https://simonwillison.net/2025/Dec/6/one-shot-decompilation/
- [OFFICIAL] Macabeus, *Can LLMs Really Do Matching Decompilation?*:
  - https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288

## Synthesized takeaways

- [SYNTH] One-shot attempts can work on simple functions, but reliability still depends on immediate compile + diff verification.
- [SYNTH] Prompt quality and context completeness (types, calling convention, exact asm) matter more than raw verbosity.
- [SYNTH] Programmatic baselines (m2c/permuter) should run first; one-shot is a fallback accelerator, not a replacement for verification.
- [SYNTH] The practical unit of work is one function or one object, not a whole executable. The article's throughput win comes from repeatedly selecting likely-easy functions, attempting a narrow source match, running the existing verifier, and moving hard cases aside.
- [SYNTH] The agent is not trusted as an oracle. Its output is useful only because the surrounding project already knows how to compile the candidate with the project compiler and compare it against target assembly.

## Workspace implications

- [REPO] Keep strict `objdiff 0` gate (`scripts/objdiff-gate.sh`) for all match claims.
- [REPO] Favor a "fast first shot" only after prompt settings validate (`scripts/validate-prompt-settings.sh`).
- [REPO] Preserve sandbox rule: no direct target-source edits during matching (`.cursor/rules/matching-decompilation-core.mdc`).
- [REPO] For `swkotor.exe`, one-shot cannot start from the packed Steam `.text`; use the Steamless-unpacked `.textV` inventory as the target code map.
- [REPO] Do not count `.incbin`, inline bytes, or whole-binary replay as one-shot decompilation. Those prove acquisition/replay only.

## What The Article Is Doing

The Chris Lewis workflow is a scheduler wrapped around an existing matching
decompilation project:

1. Generate a difficulty-ranked function queue.
2. Pick the current simplest unmatched function.
3. Build a narrow prompt from that function's assembly, context, and project
   conventions.
4. Let the agent write high-level C for that single function.
5. Compile with the known project compiler and flags.
6. Run the project's verify/diff command.
7. Commit or land only zero-diff matches; otherwise record the function as hard
   and continue elsewhere.

The important constraint is that compiler identity and verification already
exist. The agent improves throughput by producing source hypotheses, not by
recovering a whole application or certifying matches itself.

## Comparison To Current ReconstructKit/SWKOTOR Work

| Dimension | Article workflow | Current SWKOTOR state | Gap |
| --- | --- | --- | --- |
| Target bytes | Known target functions in an active decomp project | Steamless-unpacked `.textV` inventory is available | Good enough for function work; still need undecoded-range and data coverage. |
| Compiler profile | Project compiler/flags are already known or mostly known | VC7.1 and VC8 run locally; nontrivial probes still mismatch | Need broader compiler-profile corpus and source-shape sweeps before large AI loops. |
| Unit verifier | Existing project verify command | Per-function target slices plus objdiff zero are working | Need translation-unit and link-level object model later. |
| Source context | Project headers/types available | Binary inventory exists; class/global/type recovery is thin | Need real type, vtable, global, and import models. |
| Scheduling | Difficulty-ranked one-shot queue | Recovery queue exists with tags and 574 verified easy matches | Need queue classes for constructors, loops, virtual calls, switches, and CRT/library candidates. |
| Acceptance | Commit only verified source matches | Current accepted shard is 574 high-level C functions, compiled 574/574 | Full executable parity remains far away: 8047 inventory entries remain. |

## Operational Rule

Use one-shot only after the following are true for a candidate class:

1. The target slice has a valid symbolic object with relocations where needed.
2. At least one nearby/simple function in the same idiom has already matched.
3. The compile command records exact compiler banner, flags, source hash, object
   hash, and objdiff result.
4. The prompt includes only relevant context: target assembly, candidate diff,
   function prototype hypothesis, known callees/globals, and matched examples.

If any of those are missing, the work is compiler/type forensics, not one-shot
matching yet.
