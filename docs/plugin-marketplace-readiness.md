# Plugin marketplace readiness

Repeatable audit for the `matching-decompilation-re` Cursor plugin before marketplace submission.

## Run the audit

Against the default local install:

```bash
./scripts/audit-plugin-readiness.sh
```

Against an explicit plugin directory:

```bash
./scripts/audit-plugin-readiness.sh --plugin-root ~/.cursor/plugins/local/matching-decompilation-re
```

Success prints:

```text
PLUGIN_READINESS_OK
```

Failures print `PLUGIN_READINESS_FAIL count=N` and list missing/invalid items on stderr.

## What is checked

Aligned with the Cursor `review-plugin-submission` checklist:

| Section | Checks |
|---------|--------|
| Manifest | `.cursor-plugin/plugin.json` parses; `name`, `version`, `description` present; kebab-case name |
| Docs | `README.md`, `LICENSE`, `CHANGELOG.md` exist |
| Skills | Each `skills/*/SKILL.md` has `name` + `description` frontmatter |
| Commands | Each `commands/*.{md,txt}` has `name` + `description` frontmatter |
| Agents | Each `agents/*.md` has `name` + `description` frontmatter |
| Rules | Each `rules/*.{mdc,md}` has `description` frontmatter |
| Hooks | `hooks/hooks.json` parses; referenced hook scripts exist |

Optional items (not required to pass): `mcp.json`, marketplace registration in a multi-plugin repo.

## Tests and CI

Fixture-backed tests avoid depending on a local plugin install in GitHub Actions:

```bash
./tests/test-plugin-readiness-audit.sh
./scripts/run-test-suite.sh
```

CI runs the full suite via `.github/workflows/test.yml` on push and pull requests to `main`.

## Related ideation

From `docs/ideation/2026-05-29-mizuchi-next-steps-ideation.md` item **#3** — marketplace readiness pass. Plan: `docs/plans/2026-05-29-008-feat-plugin-marketplace-ci-plan.md`.
