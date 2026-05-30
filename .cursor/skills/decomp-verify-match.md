# Skill: decomp-verify-match

Purpose: enforce match-verification gates.

## Gate
Run:

```bash
./scripts/objdiff-gate.sh <target.o> prompts/<fn>/build/candidate.o
```

Only treat the function as matched when objdiff reports 0 differences.
