# Mizuchi workspace

Cursor workspace for **matching decompilation** on reverse-engineered game binaries (KOTOR / Odyssey). The goal is C that recompiles to **byte-identical** object code — verified with **objdiff 0 differences**, not readable pseudocode alone.

## Quick start

0. Install/run the package entry point directly:
   - `uvx --from git+https://<repo_url> mizuchi-cli <folder-or-binary>`
   - Local checkout equivalent: `uvx --from . mizuchi-cli <folder-or-binary>`
   - Check install/package assets: `uvx --from . mizuchi-cli self-check --json`
   - See upstream surface mapping: `uvx --from . mizuchi-cli upstream-status`
   - This runs the generic recovery orchestrator with byte-authority packaging enabled, source-task generation enabled, and the upstream-style plugin synthesis engine selected by default.
   - Outputs land under `target/mizuchi-cli/<target-id>/`: `report.json`, `byte-authority/`, `source-generation/`, `source-synthesis/`, and run-root `recovered-source/` when verified source slices exist.
   - Use `--stop-after plan-strategy` for a quick planning/package-setup pass, `--source-synthesis none` to skip compiler/object gates, or `--source-synthesis msvc --source-synthesis-vc-root <vc-root>` for MSVC-gated source matching.
1. Enable plugin **matching-decompilation-re** in Cursor → Settings → Plugins  
   Path: `~/.cursor/plugins/local/matching-decompilation-re/`
2. Read `AGENTS.md` for commands, skills, and invariants.
3. Check local command specs in `.cursor/commands/` and MCP config in `.cursor/mcp.json`.
4. Use `./scripts/decomp-cli.sh verify-surface` to assert subagents/hooks/rules/skills/commands/MCP/CLI surfaces.
5. Prove the local compile/roundtrip gate:
   - `./scripts/build-and-verify.sh --prompt prompts/roundtrip_identity --refresh-target`
   - `./scripts/decomp-cli.sh decomp-function roundtrip_identity`
   - Compile failures keep the full log and write a capped `build-and-verify.compile.summary.txt`; objdiff results use `scripts/lib/verify-objdiff.sh` so CLI, MCP, and integration gates parse the same proof format.
6. Generate one fixed one-shot trial from a response or headless runner:
   - `./scripts/decomp-cli.sh matcher fun_00148020 --response-file /tmp/response.txt`
   - Or set `MIZUCHI_MATCHER_COMMAND='your-runner {{promptFile}} {{responseFile}}'`; `run-ai-phase.sh` will parse `trial.c` and verify it through `build-and-verify.sh`.
7. Initialize or inspect the autonomous matching queue:
   - `./scripts/decomp-cli.sh import-one-shot-tasks --package target/<app>/one-shot-source --prompts-dir prompts`
   - `./scripts/decomp-cli.sh one-shot-task-coverage --package target/<app>/one-shot-source --prompts-dir prompts --queue state/queue.json`
   - `./scripts/decomp-cli.sh vacuum init --queue state/queue.json --prompts-dir prompts`
   - `./scripts/decomp-cli.sh scorer --queue state/queue.json --update-queue --out state/scores.json`
   - `./scripts/decomp-cli.sh vacuum start --queue state/queue.json --max-functions 1 --timeout 30m`
   - `./scripts/decomp-cli.sh vacuum start --queue state/queue.json --commit-after-match --max-functions 1 --timeout 30m`
   - `./scripts/decomp-cli.sh commit-verified-match --prompt prompts/<name> --dry-run`
   - `./scripts/decomp-cli.sh vacuum resume --queue state/queue.json --max-functions 1`
   - `./scripts/decomp-cli.sh vacuum reset-queue --queue state/queue.json --name <fn>`
   - `./scripts/decomp-cli.sh queue summary --queue state/queue.json`
   - `import-one-shot-tasks` converts `FUNCTION_RECONSTRUCTION_TASKS.json` entries into prompt folders with task-local byte-slice verifier commands. `one-shot-task-coverage` reports which package tasks are not imported, queued, missing candidates, unverified, matched, or integrated; it does not claim whole-app semantic source recovery unless package semantic readiness and all task proofs agree. `vacuum init` creates `state/`, `logs/`, `queue.json`, `scores.json`, and `vacuum-session.json` from prompt manifests. Queue state is separate from prompt manifests and tracks pending/matched/integrated/failed/difficult plus attempt history; scorer writes deterministic easiest-first ordering and ML-ready metadata. Vacuum detects quota/rate-limit output, writes `state/vacuum-session.json`, backs off exponentially, respects `--timeout`, and leaves the function pending for resume. Commit-after-match is opt-in and stages only verified source/proof paths.
8. Inventory installed Steam apps for app-level roundtrip readiness:
   - `./scripts/decomp-cli.sh steam-roundtrip --out target/steam-roundtrip/inventory.json > target/steam-roundtrip/inventory.md`
   - Treat only `roundtrip_evidence.byteIdentical == true` as success. Current full-app scans are evidence-gathering gates; they do not replace per-function objdiff proof.
9. Run the resumable source-parity orchestrator for the current SWKOTOR lane:
   - `./scripts/decomp-cli.sh source-parity-one-shot /run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor --resume`
   - The command locates `swkotor.exe`, prepares/unpacks the analysis image, reuses or regenerates inventory, runs the current verified matching lanes, exports/compiles recovered source, derives coverage from proof artifacts, and refreshes the next-function queue.
   - It also builds `target/source-parity-index/swkotor/`, a deterministic matched-example retrieval and strategy index for queued functions. Similarity guides candidate generation only; objdiff zero remains the acceptance gate.
   - It then builds `target/source-parity-profile/swkotor/`, selecting a diverse corpus from verified examples and sweeping available compiler/flag profiles so future source-shape generation is evidence-ranked instead of guessed.
   - It also runs `target/source-parity-synthesis/swkotor/`, which automatically emits bounded C candidates from queued function bytes and objdiff-gates them. Generated mismatches stay as negative evidence; only objdiff-zero candidates can become accepted source.
   - Progress is printed stage-by-stage; `Ctrl-C` records cancellation, and the same command resumes from completed receipts. Use `--force` to rerun all selected stages and `--json` for machine-readable progress events.
   - The current result is partial function source parity only: high-level C/C++ functions accepted by objdiff zero. It is not whole-executable source recovery.
10. Run the new package-oriented recovery orchestrator for any folder or binary:
   - `./scripts/decomp-cli.sh recover <folder-or-binary> --resume`
   - `./scripts/decomp-cli.sh recover <folder-or-binary> --stop-after plan-strategy`
   - `./scripts/decomp-cli.sh recover <folder-or-binary> --function-facts-jsonl target/<app>/function-facts.jsonl --stop-after plan-strategy`
   - `./scripts/decomp-cli.sh recover <folder-or-binary> --snapshot-existing-recovery rev1`
   - `PYTHONPATH=src python3 -m mizuchi_re.cli inspect <folder-or-binary>`
   - This is the migration path away from hardcoded shell-script orchestration. It records target identity, local capabilities, PE/ELF binary inventory, strategy lanes, resumable state, events, and reports under `target/mizuchi-recover/<target-id>/`.
   - It automatically queues source-candidate tasks from discovered function boundaries. If machine-generated function facts are supplied, it can emit external source candidates under `source-generation/`; generated candidates remain unverified until compiler and objdiff gates accept them.
   - It never asks the operator to hand-write C/C++ as a recovery input. Missing decompiler/model/programmatic source output is recorded as `needs-automatic-source-generation`, not papered over with manual source.
   - If verified recovery artifacts already exist for the same binary, `--snapshot-existing-recovery rev1` copies them into a labeled backup with a manifest.
   - Add `--byte-authority` to emit a generic byte-exact source/emitter package. That proves rebuild plumbing only; semantic source parity still requires matching-decompilation evidence.
   - Architecture doc: `docs/knowledgebase/10-architecture-runtime/recovery-orchestrator.md`.
11. For ELF game binaries with symbols, scaffold and verify exact function byte slices:
   - `./scripts/decomp-cli.sh elf-function-slice scaffold --binary <game-binary> --symbol <elf-symbol> --out target/<fn>/`
   - `./scripts/decomp-cli.sh elf-function-slice verify --binary <game-binary> --symbol <elf-symbol> --candidate-object <candidate.o> --candidate-symbol <candidate-symbol>`
12. Batch-match conservative tiny ELF functions with generated C templates:
   - `./scripts/decomp-cli.sh elf-auto-trivial --binary <game-binary> --out target/<app>/functions-auto-trivial`
   - Supports symbolic x86-64 ELF and i386 ELF targets; only functions with byte-identical verifier reports are emitted as matches.
13. Batch-match conservative tiny PE DLL exports with generated C templates:
   - `./scripts/decomp-cli.sh pe-auto-trivial --binary <game-dll> --out target/<app>/pe-export-auto-trivial`
   - This compares PE export byte slices to locally compiled object functions and, by default, emits a minimal rebuilt `exports.dll` for matched exports. It is verified source for those slices, not a full PE/COFF relink.
14. Generate a byte-exact whole-binary source fallback:
   - `./scripts/decomp-cli.sh binary-source-roundtrip --binary <game-binary> --out target/<app>/full-binary-source`
   - This emits assembler source plus an included `original.bin`, recompiles it, extracts the byte section with `objcopy`, and verifies the rebuilt bytes against the original. Add `--artifact-mode lean` to compile/extract/compare but retain only compact source/report artifacts that reference the installed original file; this is the scalable mode for broad installed-library proof runs. Both modes prove byte-identical rebuild plumbing while semantic decompilation coverage remains tracked separately.
   - For a single-command source artifact plus authority receipt:
     `./scripts/decomp-cli.sh one-shot-source --binary <game-binary> --out target/<app>/one-shot-source`
   - Add `--candidate-source candidate.c` to include a supplied C source candidate that compiles with `gcc -O2`, emits bytes to stdout, and is accepted only when it exactly matches the target binary.
   - Add `--candidate-source-dir source-tree/ --candidate-build-command 'cmd {source_dir} {output}'` to include a supplied source tree. `{source}` is the packaged single-file candidate when used, `{source_dir}` is the packaged source tree, `{output}` is the required byte output, and `{package}` is the package directory.
   - Use `--complete` for the full deliverable in one command: package directory, verified archive, and standard receipts under `target/<app>/one-shot-source/receipts`, including `deliverable.json` plus the rich archive replay receipt with pins, source candidates, gates, and candidate build recipe.
   - `authoritative` now means `authorityClass: byte-authoritative-source` and `accuracyClass: byte-exact`: both generated source forms must reproduce the original bytes, and the package-local verifier rejects failed authority gates or overbroad semantic claims.
   - Add `--archive` to also emit `target/<app>/one-shot-source.tar.gz` with SHA256 in the command output. Tar/gzip metadata is normalized, but package contents include generation-time and output-path provenance, so archives are portable and verified rather than cross-directory byte-stable.
   - Add `--result-out target/<app>/one-shot-source-result.json` to persist the top-level generation result, including standalone and archive verifier outcomes.
   - In `--complete`, `one-shot-source-result.json` is rewritten after deliverable and bundle replay so it carries final `completeStatus`, proof, deliverable, and bundle-verifier summaries.
   - The deliverable embedded inside `.deliverable.tar.gz` is marked `deliverablePhase: pre-bundle-index`; the package-side `receipts/deliverable.json` is rewritten as `deliverablePhase: final-package-index` after bundle replay records `bundleVerifier`.
   - Add `--archive-verify-out target/<app>/archive-verify.json` with `--archive` to persist the archive replay receipt during generation.
   - Add `--proof-out target/<app>/proof.json --proof-markdown-out target/<app>/proof.md` to persist aggregate proof reports during generation.
   - Add `--deliverable-out target/<app>/deliverable.json` to persist a single index over package, archive, pins, receipts, candidates, gates, recipe, and replay entrypoints.
   - `deliverable.json` includes required `authoritySummary`, a compact top-level contract for status, authority class, accuracy class, authority gates, source-candidate status, package proof, content identity, and semantic-claim boundary.
   - Add `--receipt-dir target/<app>/receipts` to write the standard receipt set: `one-shot-source-result.json`, `archive-verify.json`, `proof.json`, `proof.md`, `byte-accurate-response-proof.json`, `deliverable.json`, and post-bundle `bundle-verify.json` when a bundle is produced.
   - Verify a complete deliverable from that single index:
     `./scripts/decomp-cli.sh one-shot-source-deliverable-verify --deliverable target/<app>/one-shot-source/receipts/deliverable.json --markdown`
   - Archive and deliverable verifiers both report compact `authoritySummary` data so automation can compare the replayed contract with the deliverable contract.
   - Archive and deliverable verifiers also report and cross-check `SOURCE_ROLES.json` so portable replay distinguishes generated byte-source, generated byte-emitter, and supplied source candidates.
   - Archive and deliverable verifiers also report `PROOF_COMMANDS.json` metadata so automation can discover replay entrypoints and expected success markers from replay output, including the byte-accurate response proof command.
   - Deliverable verification cross-checks response importers, response validator, receipt refresher, response template tools, and byte-accurate response proof tools against archive replay.
   - Package-local `VERIFY.py` also validates `AUTHORITY_SUMMARY.json` against the package receipts before rebuilding sources.
   - Complete deliverable verification also pins `AUTHORITY_SUMMARY.json` with `authoritySummarySha256` and compares it against archive replay.
   - Static package validation checks that `receipts/deliverable.json` pins the same `AUTHORITY_SUMMARY.json`, that the recorded bundle file exists with matching size/SHA256, and that post-bundle `receipts/bundle-verify.json` matches the deliverable bundle verifier when complete-mode receipts are present.
   - Use `./scripts/decomp-cli.sh one-shot-source-validate --package target/<app>/one-shot-source --require-complete` to require the full complete-mode receipt chain and bundle metadata; the validation report lists each required complete-mode receipt.
   - With `--archive --require-complete`, static validation still validates the source archive package; complete-mode receipts live outside the source archive and are proven with `one-shot-source-deliverable-verify --bundle`.
   - `PACKAGE_PROOF.json` and proof Markdown include the same `AUTHORITY_SUMMARY.json` pin for audit-friendly aggregate proof; `PACKAGE_PROOF.json` also pins the response importers, response validator, and receipt refresher scripts by SHA256.
   - Verify a portable deliverable bundle:
     `./scripts/decomp-cli.sh one-shot-source-deliverable-verify --bundle target/<app>/one-shot-source.deliverable.tar.gz --markdown`
   - Portable bundles include a top-level `BUNDLE_MANIFEST.json`; bundle verification checks every listed member by size and SHA256 before replaying the deliverable.
   - `BUNDLE_MANIFEST.json` also carries `authoritySummarySha256` and `contentIdentity`; bundle verification marks the manifest failed if either pin differs from deliverable replay.
   - Bundle verification reports `bundleManifestSha256` so the outer bundle contract can be pinned by callers.
   - `one-shot-source` defaults to `--artifact-mode full` so the authority package is self-contained; use `--artifact-mode lean` only when you explicitly want compact artifacts that reference the installed original file.
   - The one-shot package writes `full-binary.S`, `full-binary.c`, `original.bin`, `binary-source-roundtrip.json`, `c-source-roundtrip.json`, `source-authority-report.json`, `one-shot-source-receipt.json`, `AUTHORITATIVE_SOURCE.md`, `AUTHORITY_SUMMARY.json`, `AUTHORITY_GATES.json`, `BINARY_EVIDENCE.json`, `CLAIMS.json`, `CONTENT_MANIFEST.json`, `EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py`, `EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py`, `EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py`, `PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py`, `FUNCTION_BOUNDARY_CANDIDATES.json`, `FUNCTION_BYTE_SLICES.json`, `FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json`, `FUNCTION_RECONSTRUCTION_TASKS.json`, `FUNCTION_SLICE_SOURCES.json`, `IMPORT_RECONSTRUCTION_CANDIDATES.py`, `IMPORT_RECONSTRUCTION_RESPONSE_JSON.py`, `REFRESH_RECONSTRUCTION_RECEIPTS.py`, `ONE_SHOT_RECONSTRUCTION_BUNDLE.json`, `ONE_SHOT_RECONSTRUCTION_REQUEST.json`, `ONE_SHOT_RECONSTRUCTION_REQUEST.md`, `PACKAGE_PROOF.json`, `PROOF_COMMANDS.json`, `PROOF_COMMANDS.sh`, `RECONSTRUCTION_RESPONSE_TEMPLATE.json`, `SEMANTIC_READINESS.json`, `SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json`, `SOURCE_INDEX.json`, `SOURCE_ROLES.json`, `TOOLCHAIN_PROVENANCE.json`, `VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py`, `VERIFIED_SOURCE_CANDIDATES.json`, `Makefile`, `README.md`, `SHA256SUMS`, `VERIFY.py`, `VERIFY.sh`, and `package-manifest.json`; with supplied candidates, it also writes `candidate-source.c` or `candidate-source-tree/`, `candidate-source-roundtrip.json`, `CANDIDATE_BUILD_RECIPE.json`, and `REPLAY_CANDIDATE.sh`.
   - `SHA256SUMS` provides conventional checksums for the stable source payload and is verified by `VERIFY.py`.
   - `SOURCE_INDEX.json` lists the source files, language, authority class, SHA256, rebuild commands, expected output hash, and semantic decompilation non-claim.
   - `SOURCE_ROLES.json` classifies each source artifact as generated assembler byte-source, generated C byte-emitter, or supplied byte-exact source candidate so automation can distinguish byte-source authority from unproven semantic recovery.
   - `SEMANTIC_READINESS.json` records the current byte-exact claim, the target semantic-source-recovery claim, available evidence, and the missing proof required before semantic recovery can be claimed.
   - `EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py` and `SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json` replay the promotion decision after reconstruction candidates are imported; baseline packages remain `not-ready` until every task is matched and semantic evidence blockers are cleared.
   - `VERIFIED_SOURCE_CANDIDATES.json` lists the emitted source candidates with `accuracyClass: byte-exact` and verifier evidence.
   - `CANDIDATE_BUILD_RECIPE.json` records the supplied-candidate replay command, source inputs, expected output hash, and claim boundary.
   - `REPLAY_CANDIDATE.sh` and `make candidate` replay only the supplied source candidate and verify its byte output.
   - `AUTHORITY_GATES.json` lists each authoritative-source gate, its pass/fail status, and the evidence files backing it.
   - `BINARY_EVIDENCE.json` records file-format, section, symbol, and function-symbol hints when local tools can extract them; these are inputs for semantic recovery, not semantic proof.
   - `FUNCTION_BOUNDARY_CANDIDATES.json` derives function-boundary candidates from binary evidence when symbol tables expose function symbols; candidates remain hints until verified against source slices.
   - `FUNCTION_BYTE_SLICES.json` resolves candidate functions to exact target byte ranges when section/address/size metadata is sufficient, recording per-slice hashes for future source matching.
   - `FUNCTION_SLICE_SOURCES.json` indexes generated C byte-emitter files under `function-slice-sources/` for resolved function slices; these replay exact slice bytes and do not claim recovered semantic logic.
   - `FUNCTION_RECONSTRUCTION_TASKS.json` indexes one-shot reconstruction task folders under `function-reconstruction-tasks/`; each task carries exact target bytes, a hash-pinned `ONE_SHOT_SOURCE_PROMPT.md`, the reference byte-emitter, and a `VERIFY_CANDIDATE.sh` acceptance gate that future candidate semantic source must satisfy before semantic source can be claimed.
   - `ONE_SHOT_RECONSTRUCTION_REQUEST.md` aggregates all reconstruction tasks into one top-level source-generation request that tells a future one-shot pass exactly which `candidate.c` files to produce.
   - `ONE_SHOT_RECONSTRUCTION_REQUEST.json` is the canonical machine-readable one-shot request with task hashes, preferred JSON response shape, and replay commands including post-import receipt refresh.
   - `ONE_SHOT_RECONSTRUCTION_BUNDLE.json` embeds each task prompt text plus the accepted response paths, replay commands, and hash-pinned response/import/replay/evaluation/receipt-refresh tools so a one-shot source pass can consume a single JSON request artifact.
   - `RECONSTRUCTION_RESPONSE_TEMPLATE.json` and `EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py` define and export the exact response skeleton a future one-shot pass should fill before import; the template pins the JSON importer, JSON validator, directory importer, and receipt refresher by SHA256.
   - `EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py` exports a JSON response with byte-accurate task-local `.text` candidates from embedded target bytes, proving the response/import/replay path can produce exact source artifacts without claiming semantic decompilation.
   - `PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py` copies the package to a temporary workspace, exports that response, preflights it, imports it, and requires all candidate replay gates to match.
   - `IMPORT_RECONSTRUCTION_CANDIDATES.py` safely imports one-shot response directories by copying only expected task-local `candidate.c` files, then refreshes replay, semantic authority evaluation, and package-local receipts.
   - `VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py` preflights a single JSON response against expected candidate paths before import.
   - `IMPORT_RECONSTRUCTION_RESPONSE_JSON.py` imports a single JSON response with either `files: {"function-reconstruction-tasks/<task>/candidate.c": "C source text"}` or structured `candidates[]` entries with constrained build overrides, then refreshes replay, semantic authority evaluation, and package-local receipts. Structured `build.command` overrides are rejected unless the importer/preflight is run with `--allow-build-command`; use that only for task-local custom compiler/linker flows that write `$CANDIDATE_OUTPUT`.
   - `REFRESH_RECONSTRUCTION_RECEIPTS.py` rewrites `SHA256SUMS`, `CONTENT_MANIFEST.json`, `CLAIMS.json`, `AUTHORITY_GATES.json`, `one-shot-source-receipt.json`, `PACKAGE_PROOF.json`, `AUTHORITY_SUMMARY.json`, `receipts/deliverable.json`, and `package-manifest.json` after candidate import. It marks the package-side deliverable as `post-candidate-import-package-index` and retires stale complete-mode bundle/result receipts because archives and portable bundles are immutable snapshots that must be regenerated separately.
   - `FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json` records reconstruction candidate replay results; it starts as `no-candidates` and is updated by `REPLAY_RECONSTRUCTION_CANDIDATES.py` with candidate source hashes, emitted byte hashes, and byte-identity status when task-local `candidate.c` files are added. Mixed matched-plus-missing responses report `partial`; only all-task byte identity reports `matched`.
   - `REPLAY_RECONSTRUCTION_CANDIDATES.py` scans reconstruction task folders after one-shot candidates are added, runs task-local acceptance gates, and emits a replay report without upgrading the semantic claim by itself.
   - `PACKAGE_PROOF.json` aggregates claims, candidates, gates, recipe summary, and replay entrypoints inside the package.
   - `TOOLCHAIN_PROVENANCE.json` records observed verifier tool paths, versions, and replay environment assumptions.
   - Package-local `README.md` gives a consumer-facing quickstart and proof/non-proof summary.
   - `CLAIMS.json` is the compact machine-readable contract: it says the package is byte-authoritative, self-contained, and verifier-backed, while explicitly not claiming full semantic decompilation.
   - `CONTENT_MANIFEST.json` records a path-independent content identity for stable source content: original bytes, generated assembler/C byte-source, Makefile, and standalone verifiers. Local receipts with output paths and generation times remain in the package but are excluded from that identity.
   - `VERIFY.sh` is package-local: from inside the package directory it runs `VERIFY.py` when Python is available, otherwise falls back to shell-only verification. It rebuilds the assembler and C byte-source outputs and prints `ONE_SHOT_SOURCE_PACKAGE_OK` when hashes match.
   - `make verify` runs the same package-local proof path; `make asm` and `make c` rebuild the two source outputs directly.
   - Replay verification from the package without regenerating it:
     `./scripts/decomp-cli.sh one-shot-source-verify --package target/<app>/one-shot-source`
   - Package verification returns package-local verifier output plus `CLAIMS.json`, `CONTENT_MANIFEST.json`, and `SOURCE_INDEX.json` summary fields.
   - Add `--markdown` for a concise human-readable package verification summary, and `--out target/<app>/package-verify.json` to persist the JSON receipt.
   - Add `--expect-content-identity <sha256>` to fail unless the package directory matches a pinned stable content identity.
   - Replay verification from the portable archive:
     `./scripts/decomp-cli.sh one-shot-source-archive-verify --archive target/<app>/one-shot-source.tar.gz`
   - Archive verification returns the archive SHA256, package root, `contentIdentity`, compact `CLAIMS.json` proof fields, verified source candidates, authority gates, optional `CANDIDATE_BUILD_RECIPE.json` summary, and `ONE_SHOT_SOURCE_PACKAGE_OK` verifier output.
   - Add `--markdown` for a concise human-readable archive verification summary.
   - Read the compact claims without rebuilding:
     `./scripts/decomp-cli.sh one-shot-source-claims --package target/<app>/one-shot-source --markdown`
     or `./scripts/decomp-cli.sh one-shot-source-claims --archive target/<app>/one-shot-source.tar.gz --markdown`
   - Add `--out target/<app>/claims-summary.json` to persist the compact claims summary.
   - Validate package structure and manifests without rebuilding:
     `./scripts/decomp-cli.sh one-shot-source-validate --package target/<app>/one-shot-source --markdown`
     or `./scripts/decomp-cli.sh one-shot-source-validate --archive target/<app>/one-shot-source.tar.gz --markdown`
   - Add `--out target/<app>/validation.json` to persist the structural validation receipt.
   - Add `--expect-content-identity <sha256>` to structural validation to fail early when the package or archive is not the pinned source-content identity.
   - Aggregate validation, claims, and replay verification:
     `./scripts/decomp-cli.sh one-shot-source-proof --archive target/<app>/one-shot-source.tar.gz --markdown --out target/<app>/proof.json`
   - Add `--markdown-out target/<app>/proof.md` to persist a human-readable proof report.
   - Clean transient verifier outputs from a package after local rebuilds:
     `./scripts/decomp-cli.sh one-shot-source-clean --package target/<app>/one-shot-source`
   - Pin either identity when replaying an archive:
     `./scripts/decomp-cli.sh one-shot-source-archive-verify --archive target/<app>/one-shot-source.tar.gz --expect-content-identity <sha256> --expect-archive-sha256 <sha256>`
   - Add `--out target/<app>/archive-verify.json` to persist the archive verification receipt.
15. Run the current app-level Steam roundtrip orchestrator:
   - `./scripts/decomp-cli.sh steam-roundtrip-run --out target/steam-roundtrip-run`
   - The runner scans installed apps, emits per-app workspaces, runs supported ELF and PE-export slice matchers, and records explicit skip reasons for unsupported binaries.
   - By default, `--full-binary-source-mode primary` emits byte-source roundtrip artifacts for each app's primary native target. Use `--full-binary-source-mode all-files` to emit and verify generated byte-source artifacts for every regular file in each selected app folder. Use `--full-binary-artifact-mode lean` for full-library scale passes without retaining duplicate app blobs or rebuilt binaries. Use `--full-binary-runner app-batch` to compile one generated assembler source per app with one section per file, which avoids thousands of compiler subprocesses on file-heavy games. Use `--semantic-match-mode never` for full-file-only coverage passes, `--full-binary-max-files <n>` or `--full-binary-max-bytes <n>` for bounded discovery passes, `--max-apps <n>` for chunking, and `--skip-existing-full-app` to resume from app workspaces whose manifest already proves full-app byte identity.
   - Each app workspace now includes `source-roundtrip-manifest.json`, which collects verified generated source/object artifacts, byte-source app-file artifacts, and rebuilt export-DLL proofs for that app. `fullAppByteIdentical: true` means every regular installed app file selected by all-files mode was recompiled from generated byte-source and matched byte-for-byte. Semantic decompilation coverage remains separate and is represented by the ELF/PE matched function counts and source bundles.
   - Recompile and re-verify any generated app source manifest independently:
     `./scripts/decomp-cli.sh steam-roundtrip-verify-manifest --manifest target/<app>/source-roundtrip-manifest.json --out target/<app>/manifest-verify`
   - Classify exactly what the generated source is authoritative for:
     `./scripts/decomp-cli.sh source-authority-report --input target/<app>/source-roundtrip-manifest.json`
   - Summarize saved manifests against the current Steam app list:
     `./scripts/decomp-cli.sh steam-roundtrip-progress --search-root target`
   - For faster iterative PE passes, add `--max-pe-binaries <n>` and `--max-pe-binaries-per-app <n>`; use `--pe-rebuild-mode never` to skip expensive rebuilt DLL verification during discovery; use `--matcher-timeout <seconds>` to keep broad scans from stalling on one binary; omit limits for an exhaustive PE DLL scan.
16. For a new function:
   - `./scripts/bootstrap-re-pipeline.sh --prompt prompts/<fn>/` — initialize required prompt files
   - Use upstream-style prompt folders with assembly, target object path, and compiler script context.
   - `/decomp-prompt` — scaffold or refine `prompts/<fn>/` (or copy `prompts/_template/` manually)
   - `/decomp-function` — programmatic → AI matching loop
   - `/decomp-integrate` — after objdiff 0, land C in the project

## Knowledgebase

Layered docs under `docs/knowledgebase/`:

| Layer | Topic |
|-------|--------|
| `00-intent` | Goals and non-goals |
| `10-architecture-runtime` | recovery orchestration |
| `20-domain-theory` | Matching decompilation concepts |
| `50-execution` | Step-by-step playbook |
| `90-meta` | Evidence caveats |

Plugin reference: `~/.cursor/plugins/local/matching-decompilation-re/docs/`

## Upstream Mizuchi

Full daemon pipeline: [github.com/macabeus/mizuchi](https://github.com/macabeus/mizuchi)

- `mizuchi.example.yaml` at workspace root (copy to `mizuchi.yaml` in your decomp project)
- Prompt folders: `prompts/<name>/` with `prompt.md` + strict `settings.yaml` (`functionName`, `targetObjectPath`, `asm`)
- Case metadata: `case.yaml` records proof target, target family, optional local target/candidate sources, and compiler command without violating Mizuchi's strict `settings.yaml`
- Example scaffold: `prompts/fun_00148020/` (12-byte Xbox getter from assembly)
- Local proof fixture: `prompts/roundtrip_identity/` rebuilds target and candidate objects and verifies byte identity on this host
- AI verification tool: `compile_and_view_assembly`

## Research

Workflow based on [Can LLMs Really Do Matching Decompilation?](https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288) (benchmark sections excluded from this packaging).
