# Skill: ghidra-re-workflow

Purpose: drive Ghidra/AgentDecompile discovery for one target function.

## Inputs
- Program path (optional)
- Symbol/address/string needle

## Output
- Function address + symbol
- Required type/context list
- Caller/callee summary
- Exportable asm context for prompt building

Exploration only; never claim a binary match.
