# fcn.006ca30b

Imported from a one-shot-source function reconstruction task.

- Package: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source`
- Task: `function-reconstruction-tasks/0033_fcn_006ca30b`
- Target bytes: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source/function-reconstruction-tasks/0033_fcn_006ca30b/target.bin`
- Verifier: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source/function-reconstruction-tasks/0033_fcn_006ca30b/VERIFY_CANDIDATE.sh`
- Target SHA256: `afe6a740cfd48a2565e5615f44233eb8f651d7b7075bc324571ad1f13e5a3d2f`

Success requires `build-and-verify.sh` to run the task verifier and produce byte-identical target/candidate bytes.

## Original Task Prompt

# One-shot semantic source prompt: fcn.006ca30b

Produce a single `candidate.c` file for this function slice.

Hard requirements:
- Do not emit explanations, markdown fences, build logs, or alternate files.
- The output must be C source only, intended to compile as one function-level translation unit.
- The compiled `.text` bytes must match `target.bin` exactly.
- If exact semantic recovery is impossible from the evidence here, prefer a minimal honest candidate that can be rejected by `VERIFY_CANDIDATE.sh` rather than inventing unsupported semantics.

Evidence available in this task:
- Function name hint: `fcn.006ca30b`
- Section: `.text`
- Address hint: `6ca30b`
- Target size: `83` bytes
- Target SHA256: `afe6a740cfd48a2565e5615f44233eb8f651d7b7075bc324571ad1f13e5a3d2f`
- Target bytes file: `function-reconstruction-tasks/0033_fcn_006ca30b/target.bin`
- Reference byte-emitter source: `function-slice-sources/0033_fcn_006ca30b.c`

Acceptance command:
- Save your output as `candidate.c` in this task directory.
- Run `./VERIFY_CANDIDATE.sh`.
- Success requires `FUNCTION_RECONSTRUCTION_CANDIDATE_OK`.

Claim boundary:
This prompt is a semantic reconstruction request. The package does not claim semantic source recovery unless the candidate passes the acceptance command and the result is recorded as verified evidence.
