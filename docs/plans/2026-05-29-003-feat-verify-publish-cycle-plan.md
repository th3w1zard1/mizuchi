---
title: "feat: Verify pipeline parity and publish snapshot"
status: active
type: feat
created: 2026-05-29
origin: none
---

## Summary

Run full parity verification gates (bootstrap, smoke, surface, cli verify) on the current ReconstructKit workspace and publish a timestamped snapshot to a fresh GitHub repository with tracked remote. This is an operational ritual cycle to prove workspace surface integrity before upstream decomp guide integration resumes.

## Problem Frame

The ReconstructKit workspace has been bootstrapped with decomp pipeline infrastructure (skills, agents, MCP wiring, CLI entrypoints). Prior cycles have established parity invariants. This cycle validates those invariants remain green and publishes proof-of-state as a versioned artifact.

## Scope Boundaries

### In Scope

- Execute all four parity verification gates in sequence.
- Capture pass/fail status for each gate.
- Publish current branch to a fresh GitHub repository.
- Track the new remote as `originN` (incrementing from prior cycle).
- Confirm WORKSPACE_SURFACE_OK state before proceeding to next pipeline stage.

### Out of Scope

- Code changes or feature implementation.
- Modifying verification scripts or their output signals.
- Decomp guide implementation beyond operational scaffolding.

### Deferred to Follow-Up Work

- Continuous integration automation to run parity gates on every commit.
- Metrics dashboarding of gate pass rates across sessions.

## Key Technical Decisions

- Parity gates run in order: bootstrap → smoke → surface → cli verify.
- Each gate must exit 0 and output `WORKSPACE_SURFACE_OK` to proceed.
- Stop immediately on first gate failure and report diagnostic.
- Publish only when all gates pass; use fresh repo per cycle for artifact isolation.
- Remote naming: extract highest `originN` suffix from `git remote`, increment, add new remote `originN+1`.

## System-Wide Impact

- **Workspace state:** Remains immutable during verification; no code changes.
- **Git remotes:** Adds new `originN` tracking remote; does not modify `origin` or existing remotes.
- **GitHub:** Creates one new timestamped repository per cycle; no modifications to existing published repos.
- **Pipeline:** Parity check is blocking gate before doc-review and agent-native-audit stages.

## Implementation Units

### U1. Execute parity verification gates

**Goal:** Run all four verification gates in sequence; report results.

**Requirements:** All gates must exit 0 and output `WORKSPACE_SURFACE_OK` individually.

**Dependencies:** None.

**Files:**
- tests/bootstrap_re_pipeline_test.sh (execute)
- tests/lfg_smoke_test.sh (execute)
- tests/verify_workspace_surface_test.sh (execute)
- scripts/decomp-cli.sh verify-surface (execute)
- No files modified; scripts already exist and are stable.

**Approach:**

1. Change to repository root: `cd /home/brunner56/Workspaces/ReconstructKit`
2. Run `bash tests/bootstrap_re_pipeline_test.sh`; capture exit code and last output line.
3. Run `bash tests/lfg_smoke_test.sh`; capture exit code and last output line.
4. Run `bash tests/verify_workspace_surface_test.sh`; capture exit code and last output line.
5. Run `bash scripts/decomp-cli.sh verify-surface`; capture exit code and last output line.
6. If all four exit with code 0 AND all four output lines contain `WORKSPACE_SURFACE_OK`, proceed to U2.
7. If any gate fails: report the gate name, exit code, and output; stop and wait for user input.

**Patterns to follow:**

- Existing test scripts in `tests/` use bash, exit 0 on success, produce final status line.
- `decomp-cli.sh` verify-surface subcommand follows same pattern.

**Test scenarios:**

- Happy path: all four gates pass (expected state).
- Single gate failure: first gate fails; verify stop and report is clean.
- Output verification: final output line contains `WORKSPACE_SURFACE_OK` (not just exit 0).

**Verification:**

- All gates reported green: "PASS: All parity gates green."
- Any gate reported red: "FAIL: Gate <name> failed with exit code <N>. Output: <line>."

---

### U2. Compute next remote index and publish snapshot

**Goal:** Add new remote and push current branch to fresh GitHub repository.

**Requirements:** Fresh repo created; remote added with next available `originN` index; current branch pushed successfully.

**Dependencies:** U1 (parity gates all passing).

**Files:**
- No files modified; pure git operations.

**Approach:**

1. Parse existing remotes: `git remote | awk '/^origin[0-9]+$/{sub("origin",""); print $0}' | sort -n | tail -1`.
2. Compute next index: `nextIndex = lastIndex + 1`.
3. If no prior remotes found (first-time setup), default to `originN = 1`.
4. Create new GitHub repo with name `reconkit-<YYYYMMDD-HHMMSS>` using `gh repo create`.
5. Extract repo URL from `gh` output.
6. Add remote: `git remote add origin<nextIndex> <repo-url>`.
7. Push current branch: `git push --set-upstream origin<nextIndex> HEAD`.
8. Confirm push succeeded (exit 0).

**Patterns to follow:**

- Remote naming convention: `origin1`, `origin2`, ..., `origin18` (incremented per cycle).
- Repo naming: `reconkit-YYYYMMDD-HHMMSS` (timestamp at push time).
- Use `gh repo create --confirm` to skip interactive prompts (no user input in pipeline mode).

**Technical design (directional):**

```bash
# Parse highest existing originN
LAST_INDEX=$(git remote | awk '/^origin[0-9]+$/{sub("origin",""); print $0}' | sort -n | tail -1)
NEXT_INDEX=${LAST_INDEX:-0}
NEXT_INDEX=$((NEXT_INDEX + 1))

# Create repo and push
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
REPO_NAME="reconkit-$TIMESTAMP"
gh repo create "$REPO_NAME" --confirm --private || --public  # adjust privacy per project
REPO_URL=$(gh repo view "$REPO_NAME" --json url --jq .url)

git remote add "origin${NEXT_INDEX}" "$REPO_URL"
git push --set-upstream "origin${NEXT_INDEX}" HEAD
```

(This is directional; implementer should confirm privacy setting and error handling.)

**Test scenarios:**

- Happy path: remote added, push succeeds, new repo is reachable.
- Remote index collision: `originN` already exists; report and fail cleanly (should not happen with increment logic).
- GitHub API failure: `gh repo create` times out; report and stop.

**Verification:**

- New remote present in `git remote` output: `git remote | grep origin<nextIndex>`.
- Push succeeded: branch reachable at GitHub URL.
- Repo URL output for logging: `gh repo view <repo-name> --json url`.

---

### U3. Confirm pipeline readiness and return control

**Goal:** Report final parity state and pass control to next pipeline stage (ce-doc-review).

**Requirements:** Both U1 and U2 passed; parity and publish confirmed.

**Dependencies:** U1, U2.

**Files:**
- No files modified.

**Approach:**

1. Confirm all gates passed in U1: report "WORKSPACE_SURFACE_OK".
2. Confirm remote and push succeeded in U2: report remote name and repo URL.
3. Set exit code 0 to signal success to LFG pipeline.
4. Return control to LFG orchestrator.

**Test scenarios:**

- Happy path: all confirmations printed, exit 0, pipeline continues.

**Verification:**

- Exit code is 0.
- Output includes final status ("WORKSPACE_SURFACE_OK" + remote name + URL).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|-----------|
| GitHub API rate limit or transient failure | Retry once; if second attempt fails, report and stop. Pipeline can resume after manual GitHub check. |
| New `originN` remote index collision | Increment logic should never collide, but if it does, report and fail cleanly. |
| Parity gate flakiness | If a gate produces inconsistent results, investigate in next session; this cycle stops on failure. |

## Deferred Implementation Notes

- Exact error messages and log format are implementation details; ensure they are clear and actionable.
- GitHub privacy setting (--public vs implicit default) should be confirmed with team policy.
- Retry strategy on transient GitHub failures can be refined in follow-up if pattern emerges.

## Verification

The unit is complete when:
- All four parity gates report exit 0 + output contains "WORKSPACE_SURFACE_OK".
- New GitHub remote is reachable and current branch is pushed.
- Exit code is 0 and control returns to LFG orchestrator.
- Next pipeline stage (ce-doc-review) can proceed without blocker.
