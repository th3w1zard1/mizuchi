# fcn.008b490a

Imported from a one-shot-source function reconstruction task.

- Package: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source`
- Task: `function-reconstruction-tasks/0041_fcn_008b490a`
- Target bytes: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source/function-reconstruction-tasks/0041_fcn_008b490a/target.bin`
- Verifier: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source/function-reconstruction-tasks/0041_fcn_008b490a/VERIFY_CANDIDATE.sh`
- Target SHA256: `f9905632ee9c87aba1192cae401a0387f228cd752dc33b173b1d04d324e04a4a`

Success requires `build-and-verify.sh` to run the task verifier and produce byte-identical target/candidate bytes.

## Original Task Prompt

# One-shot semantic source prompt: fcn.008b490a

Produce a single `candidate.c` file for this function slice.

Hard requirements:
- Do not emit explanations, markdown fences, build logs, or alternate files.
- The output must be C source only, intended to compile as one function-level translation unit.
- The compiled `.text` bytes must match `target.bin` exactly.
- If exact semantic recovery is impossible from the evidence here, prefer a minimal honest candidate that can be rejected by `VERIFY_CANDIDATE.sh` rather than inventing unsupported semantics.

Evidence available in this task:
- Function name hint: `fcn.008b490a`
- Section: `.bind`
- Address hint: `8b490a`
- Target size: `88` bytes
- Target SHA256: `f9905632ee9c87aba1192cae401a0387f228cd752dc33b173b1d04d324e04a4a`
- Target bytes file: `function-reconstruction-tasks/0041_fcn_008b490a/target.bin`
- Reference byte-emitter source: `function-slice-sources/0041_fcn_008b490a.c`

Acceptance command:
- Save your output as `candidate.c` in this task directory.
- Run `./VERIFY_CANDIDATE.sh`.
- Success requires `FUNCTION_RECONSTRUCTION_CANDIDATE_OK`.

Claim boundary:
This prompt is a semantic reconstruction request. The package does not claim semantic source recovery unless the candidate passes the acceptance command and the result is recorded as verified evidence.
