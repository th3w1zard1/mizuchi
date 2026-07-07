# Installable `mizuchi-cli`

The package exposes an installable front door for one-shot binary recovery:

```sh
uvx --from git+https://<repo_url> mizuchi-cli <path/to/binary-or-folder>
```

Local checkout smoke path:

```sh
uvx --from . mizuchi-cli --help
uvx --from . mizuchi-cli self-check --json
uvx --from . mizuchi-cli upstream-status
uvx --from . mizuchi-cli <path/to/binary-or-folder> --stop-after plan-strategy
```

## Default Behavior

`mizuchi-cli <target>` is intentionally a thin front door over the generic Python
recovery orchestrator, not a separate pipeline:

- Resolves the target identity and work directory under `target/mizuchi-cli/<target-id>/`.
- Exports binary/app context.
- Builds binary inventory and function candidates.
- Generates source-reconstruction tasks.
- Runs bounded source synthesis with the upstream-style plugin lifecycle by default.
- Emits byte-authority packaging by default.
- Writes `report.json` plus run-root `recovered-source/` when verified source slices exist.

The default source-synthesis mode is bounded (`--source-synthesis-limit 50`), so
the installable CLI produces evidence without silently launching unbounded
whole-program matching.

## Upstream Core Mapping

The upstream TypeScript Mizuchi repo provides these core surfaces:

- YAML config loading and path resolution.
- Prompt-folder loading.
- Setup, programmatic, AI-powered, and post-match plugin phases.
- Compiler, m2c, decomp-permuter, objdiff, Claude-runner, and integrator plugins.
- HTML/JSON run reports and Decomp Atlas indexing/UI.

This fork currently maps those core concepts as follows:

- `src/mizuchi_re/plugin_pipeline.py` ports the setup/programmatic/retry/post-match lifecycle.
- `src/mizuchi_re/source_plugin_runner.py` uses that lifecycle for generated source candidates and objdiff/code-slice acceptance.
- `src/mizuchi_re/pipeline.py` provides the target-level recovery orchestrator and now publishes plugin recovered-source output at the run root.
- `src/mizuchi_re/mizuchi_cli.py` provides the installable one-shot binary front door.
- `vendor/upstream-mizuchi/` is the vendored upstream source at `macabeus/mizuchi` main `218ecfe`.

Known remaining upstream gaps:

- Full upstream prompt-folder matching mode is still exposed through the existing workspace scripts and `mizuchi-recover` subcommands, not the default `mizuchi-cli <binary>` path.
- Decomp Atlas UI/indexing is vendored but not exposed as an installable Python command.
- Claude-runner integration is not part of the default binary path; current default is deterministic/programmatic source generation plus compiler/object gates.
- Whole-program semantic source parity remains unproved. Byte-authority packages and source-slice matches are evidence, not completion.

`mizuchi-cli run`, `mizuchi-cli atlas`, and `mizuchi-cli index-codebase` fail with
an explicit guard message instead of pretending that the upstream TypeScript UI
commands are packaged in the Python front door. Use `mizuchi-cli upstream-status`
for the current mapping.

## Claim Boundary

`mizuchi-cli` is allowed to emit byte-authoritative full-binary source fallbacks
and bounded recovered source slices. It must not claim whole-program semantic
source recovery unless every required source slice is generated, compiled, and
object/text compared under the relevant toolchain profile.
