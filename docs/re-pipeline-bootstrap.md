# RE Pipeline Bootstrap

Initialize a new prompt folder before running programmatic or AI matching phases.

```bash
./scripts/bootstrap-re-pipeline.sh --prompt prompts/fun_00148020/
```

Expected output:

```text
RE_BOOTSTRAP_OK prompt=prompts/fun_00148020/
```

Then continue with existing scripts, for example:

```bash
./scripts/run-programmatic-phase.sh --prompt prompts/fun_00148020/
```
