# Template prompt folder

Copy this directory to `prompts/<your-function-name>/` and fill in real values from Ghidra + your build tree.

**Do not run ReconstructKit on `_template` as-is** — it is documentation only.

## Checklist

- [ ] `functionName` matches the symbol in `targetObjectPath`
- [ ] `asm` is the full function from the **same build** as the golden `.o`
- [ ] `prompt.md` Objective cites **0 objdiff differences**
- [ ] Types / structs in prompt match `getContextScript` output (m2ctx)
