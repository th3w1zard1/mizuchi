---
description: Scout a function in Ghidra/AgentDecompile and export match-ready context.
---

Use `ghidra-binary-scout` to discover function-level reverse-engineering context before prompt authoring.

## Inputs

- Optional symbol, address, or string needle.
- Optional `program_path` (for multi-program projects).

## Procedure

1. Resolve target binary in AgentDecompile.
2. Locate target function (`search-everything`, `get-function`, `get-call-graph`).
3. Capture:
   - address + symbol
   - asm body excerpt
   - required structs/types
   - callers/callees
4. Write the scout output in chat using this shape:

```markdown
## Scout: <function>
- **Address / symbol:**
- **Program path:**
- **Asm excerpt:**
- **Types needed:**
- **Callers / callees:**
- **Suggested prompt folder:** `prompts/<name>/`
```

Never claim a match in this command. This command is exploration only.
