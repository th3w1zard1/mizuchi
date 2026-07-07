# fcn.00574aab

Imported from a one-shot-source function reconstruction task.

- Package: `/run/media/brunner56/MyBook/Workspaces/Mizuchi/target/swkotor-one-shot-source`
- Task: `function-reconstruction-tasks/0018_fcn_00574aab`
- Target bytes: `/run/media/brunner56/MyBook/Workspaces/Mizuchi/target/swkotor-one-shot-source/function-reconstruction-tasks/0018_fcn_00574aab/target.bin`
- Verifier: `/run/media/brunner56/MyBook/Workspaces/Mizuchi/target/swkotor-one-shot-source/function-reconstruction-tasks/0018_fcn_00574aab/VERIFY_CANDIDATE.sh`
- Target SHA256: `a513260638f7e3a8d5ca261ecfb1cc2ef410f5c35f95396c00110b9c517d2bf9`

Success requires `build-and-verify.sh` to run the task verifier and produce byte-identical target/candidate bytes.

## Original Task Prompt

# One-shot semantic source prompt: fcn.00574aab

Produce a single `candidate.c` file for this function slice.

Hard requirements:
- Do not emit explanations, markdown fences, build logs, or alternate files.
- The output must be C source only, intended to compile as one function-level translation unit.
- The compiled `.text` bytes must match `target.bin` exactly.
- If exact semantic recovery is impossible from the evidence here, prefer a minimal honest candidate that can be rejected by `VERIFY_CANDIDATE.sh` rather than inventing unsupported semantics.

Evidence available in this task:
- Function name hint: `fcn.00574aab`
- Section: `.text`
- Address hint: `574aab`
- Target size: `137` bytes
- Target SHA256: `a513260638f7e3a8d5ca261ecfb1cc2ef410f5c35f95396c00110b9c517d2bf9`
- Target bytes file: `function-reconstruction-tasks/0018_fcn_00574aab/target.bin`
- Reference byte-emitter source: `function-slice-sources/0018_fcn_00574aab.c`

Acceptance command:
- Save your output as `candidate.c` in this task directory.
- Run `./VERIFY_CANDIDATE.sh`.
- Success requires `FUNCTION_RECONSTRUCTION_CANDIDATE_OK`.

Claim boundary:
This prompt is a semantic reconstruction request. The package does not claim semantic source recovery unless the candidate passes the acceptance command and the result is recorded as verified evidence.
