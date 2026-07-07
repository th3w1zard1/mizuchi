# int.004bc84e

Imported from a one-shot-source function reconstruction task.

- Package: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source`
- Task: `function-reconstruction-tasks/0007_int_004bc84e`
- Target bytes: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source/function-reconstruction-tasks/0007_int_004bc84e/target.bin`
- Verifier: `/run/media/brunner56/MyBook/Workspaces/ReconstructKit/target/swkotor-one-shot-source/function-reconstruction-tasks/0007_int_004bc84e/VERIFY_CANDIDATE.sh`
- Target SHA256: `15a5e3a076500bda6279c58eb0bd42d9415d501c7e7062946a1d774647787e06`

Success requires `build-and-verify.sh` to run the task verifier and produce byte-identical target/candidate bytes.

## Original Task Prompt

# One-shot semantic source prompt: int.004bc84e

Produce a single `candidate.c` file for this function slice.

Hard requirements:
- Do not emit explanations, markdown fences, build logs, or alternate files.
- The output must be C source only, intended to compile as one function-level translation unit.
- The compiled `.text` bytes must match `target.bin` exactly.
- If exact semantic recovery is impossible from the evidence here, prefer a minimal honest candidate that can be rejected by `VERIFY_CANDIDATE.sh` rather than inventing unsupported semantics.

Evidence available in this task:
- Function name hint: `int.004bc84e`
- Section: `.text`
- Address hint: `4bc84e`
- Target size: `193` bytes
- Target SHA256: `15a5e3a076500bda6279c58eb0bd42d9415d501c7e7062946a1d774647787e06`
- Target bytes file: `function-reconstruction-tasks/0007_int_004bc84e/target.bin`
- Reference byte-emitter source: `function-slice-sources/0007_int_004bc84e.c`

Acceptance command:
- Save your output as `candidate.c` in this task directory.
- Run `./VERIFY_CANDIDATE.sh`.
- Success requires `FUNCTION_RECONSTRUCTION_CANDIDATE_OK`.

Claim boundary:
This prompt is a semantic reconstruction request. The package does not claim semantic source recovery unless the candidate passes the acceptance command and the result is recorded as verified evidence.
