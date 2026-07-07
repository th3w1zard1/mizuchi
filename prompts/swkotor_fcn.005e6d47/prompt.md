# fcn.005e6d47

Imported from a one-shot-source function reconstruction task.

- Package: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source`
- Task: `function-reconstruction-tasks/0022_fcn_005e6d47`
- Target bytes: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source/function-reconstruction-tasks/0022_fcn_005e6d47/target.bin`
- Verifier: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source/function-reconstruction-tasks/0022_fcn_005e6d47/VERIFY_CANDIDATE.sh`
- Target SHA256: `339fd29ee59539fb9c9f085122d5cebec74cd2dc56665b27a67083f1edb66b01`

Success requires `build-and-verify.sh` to run the task verifier and produce byte-identical target/candidate bytes.

## Original Task Prompt

# One-shot semantic source prompt: fcn.005e6d47

Produce a single `candidate.c` file for this function slice.

Hard requirements:
- Do not emit explanations, markdown fences, build logs, or alternate files.
- The output must be C source only, intended to compile as one function-level translation unit.
- The compiled `.text` bytes must match `target.bin` exactly.
- If exact semantic recovery is impossible from the evidence here, prefer a minimal honest candidate that can be rejected by `VERIFY_CANDIDATE.sh` rather than inventing unsupported semantics.

Evidence available in this task:
- Function name hint: `fcn.005e6d47`
- Section: `.text`
- Address hint: `5e6d47`
- Target size: `187` bytes
- Target SHA256: `339fd29ee59539fb9c9f085122d5cebec74cd2dc56665b27a67083f1edb66b01`
- Target bytes file: `function-reconstruction-tasks/0022_fcn_005e6d47/target.bin`
- Reference byte-emitter source: `function-slice-sources/0022_fcn_005e6d47.c`

Acceptance command:
- Save your output as `candidate.c` in this task directory.
- Run `./VERIFY_CANDIDATE.sh`.
- Success requires `FUNCTION_RECONSTRUCTION_CANDIDATE_OK`.

Claim boundary:
This prompt is a semantic reconstruction request. The package does not claim semantic source recovery unless the candidate passes the acceptance command and the result is recorded as verified evidence.
