# fcn.006632c1

Imported from a one-shot-source function reconstruction task.

- Package: `/run/media/brunner56/MyBook/Workspaces/Mizuchi/target/swkotor-one-shot-source`
- Task: `function-reconstruction-tasks/0030_fcn_006632c1`
- Target bytes: `/run/media/brunner56/MyBook/Workspaces/Mizuchi/target/swkotor-one-shot-source/function-reconstruction-tasks/0030_fcn_006632c1/target.bin`
- Verifier: `/run/media/brunner56/MyBook/Workspaces/Mizuchi/target/swkotor-one-shot-source/function-reconstruction-tasks/0030_fcn_006632c1/VERIFY_CANDIDATE.sh`
- Target SHA256: `759b7977ad9ecdf6c9394af5b6fa09d3d5dd628c4622925e96ea5d738ac38bc8`

Success requires `build-and-verify.sh` to run the task verifier and produce byte-identical target/candidate bytes.

## Original Task Prompt

# One-shot semantic source prompt: fcn.006632c1

Produce a single `candidate.c` file for this function slice.

Hard requirements:
- Do not emit explanations, markdown fences, build logs, or alternate files.
- The output must be C source only, intended to compile as one function-level translation unit.
- The compiled `.text` bytes must match `target.bin` exactly.
- If exact semantic recovery is impossible from the evidence here, prefer a minimal honest candidate that can be rejected by `VERIFY_CANDIDATE.sh` rather than inventing unsupported semantics.

Evidence available in this task:
- Function name hint: `fcn.006632c1`
- Section: `.text`
- Address hint: `6632c1`
- Target size: `370` bytes
- Target SHA256: `759b7977ad9ecdf6c9394af5b6fa09d3d5dd628c4622925e96ea5d738ac38bc8`
- Target bytes file: `function-reconstruction-tasks/0030_fcn_006632c1/target.bin`
- Reference byte-emitter source: `function-slice-sources/0030_fcn_006632c1.c`

Acceptance command:
- Save your output as `candidate.c` in this task directory.
- Run `./VERIFY_CANDIDATE.sh`.
- Success requires `FUNCTION_RECONSTRUCTION_CANDIDATE_OK`.

Claim boundary:
This prompt is a semantic reconstruction request. The package does not claim semantic source recovery unless the candidate passes the acceptance command and the result is recorded as verified evidence.
