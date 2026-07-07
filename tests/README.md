# Mizuchi tests

Shell-based integration and unit tests for the matching-decompilation workspace.
Run from the repository root unless noted.

## Quick smoke

```bash
./scripts/verify-workspace-surface.sh
./tests/queue_schema_test.sh
./tests/vacuum_integration_test.sh
```

## Autonomous loop (Cycle 3)

| Test | What it checks |
|------|----------------|
| `queue_schema_test.sh` | Queue JSON schema load/save and state transitions |
| `scorer_test.sh` | Heuristic scoring and queue ordering |
| `matcher_test.sh` | One-shot response parsing |
| `build_and_verify_test.sh` | Compile + byte verify for fixture prompts |
| `vacuum_test.sh` | Vacuum orchestrator backoff and state updates |
| `init_vacuum_test.sh` | `vacuum init` seeds queue from `prompts/` |
| `vacuum_integration_test.sh` | End-to-end init → score → verify → vacuum start |

## Verification tiers (source parity Phase 1)

| Test | What it checks |
|------|----------------|
| `package_verify_tier_test.sh` | `verificationTier` / `acceptanceGate` mapping |
| `package_verify_run_timeout_test.sh` | Subprocess timeout kills process tree |

## Recovery / PE pipelines

PE and Mach-O recovery tests require local game binaries or fixtures. They skip
when assets are missing. See each script header for env vars.

## CLI and workspace

| Test | What it checks |
|------|----------------|
| `mizuchi_cli_frontdoor_test.sh` | `mizuchi` CLI entrypoints |
| `decomp_cli_validate_test.sh` | Prompt folder validation |
| `verify_workspace_surface_test.sh` | Required scripts and docs exist |

## Running everything

There is no single mega-runner. Typical CI-style pass:

```bash
for t in tests/*_test.sh tests/test-*.sh; do
  echo "== $t =="
  bash "$t" || exit 1
done
```

Skip slow or asset-heavy tests when developing the vacuum loop only.
