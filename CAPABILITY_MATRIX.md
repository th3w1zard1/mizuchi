---
title: "Agent Capability Matrix"
description: "Centralized reference for all agent-accessible operations in the Mizuchi workspace"
updated: 2026-05-29
---

# Mizuchi Agent Capability Matrix

## Overview

This matrix documents all operations available to agents in the Mizuchi matching-decompilation workspace. Each agent has distinct permissions and tool access defined below.

## Capability Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Agent can perform this operation |
| ❌ | Agent cannot perform this operation |
| 🔍 | Agent can query but not modify |
| 🔒 | Agent can perform with verification gate |

## Agent Operations Matrix

| Agent | Scout | Prompt | Decompose | Integrate | Verify | Update State | Context |
|-------|-------|--------|-----------|-----------|--------|--------------|---------|
| **ghidra-binary-scout** | ✅ | 🔍 | ❌ | ❌ | ❌ | ❌ | 🔍 |
| **decomp-prompt-architect** | 🔍 | ✅ | ❌ | ❌ | 🔍 | ✅ | ✅ |
| **decomp-function-agent** | 🔍 | ✅ | ✅ | 🔒 | ✅ | ✅ | ✅ |

## Detailed Capability Definitions

### Scout Operations
- **Can invoke:** `/ghidra-scout` command to discover binary functions
- **Can access:** AgentDecompile MCP tools (`search-everything`, `get-function`, `get-call-graph`)
- **Can export:** Assembly, types, call graphs, cross-references
- **Can read:** `context/`, `docs/`, `prompts/*/`

### Prompt Operations
- **Can create:** New `prompts/<name>/prompt.md` files
- **Can edit:** Existing `prompts/*/prompt.md` and `prompts/*/settings.yaml`
- **Can validate:** Prompt folder structure via `/decomp-prompt` command
- **Can read/write:** `prompts/*/notes.md` for metadata and progress tracking

### Decompose Operations
- **Can invoke:** `/decomp-function` for full end-to-end matching
- **Can compile:** C code to object files using `compile_and_view_assembly`
- **Can iterate:** Compile → objdiff → refine loop
- **Can cache:** Intermediate results in `prompts/<name>/build/`
- **Can log:** Progress to `prompts/*/notes.md`

### Integrate Operations
- **Can invoke:** `/decomp-integrate` command to land matches
- **Constraint:** Only after objdiff returns 0 differences
- **Can modify:** Source tree (with verification gate)
- **Can update:** Integration status in notes.md

### Verify Operations
- **Can invoke:** `run_objdiff` MCP tool
- **CLI parity:** `./scripts/decomp-cli.sh run-objdiff <target.o> <candidate.o>`
- **Can query:** `get_workspace_context` for current state
- **Can read:** Build artifacts, target object files
- **Gate behavior:** Stop and report on first mismatch

### Update State Operations
- **Can modify:** `prompts/*/notes.md` files
- **Can track:** Status (matched/integrated/in_progress/blocked)
- **Can update:** Metadata (last_updated, tier, integrator_hints)
- **Can log:** Objdiff results and diagnostics

### Context Operations
- **Can query:** `get_workspace_context()` on startup
- **Can query:** `list_prompts()` to discover work queue
- **CLI parity:** `./scripts/decomp-cli.sh list-prompts [status=...]`
- **CLI parity:** `./scripts/decomp-cli.sh inject-context <agent-name> [--json]`
- **Can read:** Workspace metrics and status
- **Scope:** Read-only; no modifications via context queries

## MCP Tool Availability

All agents have access to:
- `get_workspace_context()` — Query workspace state (no args)
- `list_prompts(status=<matched|integrated|in_progress|blocked>)` — List prompts by status
- `run_objdiff(target_o, candidate_o)` — Verify match (returns JSON)

Agent-specific tools:
- **ghidra-binary-scout:** AgentDecompile MCP (search-everything, get-function, get-call-graph, match-function)
- **decomp-prompt-architect:** Decomp Atlas MCP (index, search, export-similar)
- **decomp-function-agent:** All above, plus Mizuchi `compile_and_view_assembly`

## Slash Commands by Agent

| Command | ghidra-scout | prompt-architect | function-agent |
|---------|--------------|------------------|-----------------|
| `/help` | ✅ | ✅ | ✅ |
| `/ghidra-scout` | ✅ | ✅ | ✅ |
| `/decomp-prompt` | ❌ | ✅ | ✅ |
| `/decomp-atlas` | ❌ | ✅ | ✅ |
| `/decomp-function` | ❌ | ❌ | ✅ |
| `/decomp-integrate` | ❌ | ❌ | 🔒 |

## File Access by Agent

| Path | ghidra-scout | prompt-architect | function-agent | Type |
|------|--------------|------------------|-----------------|------|
| `prompts/*/prompt.md` | 🔍 | ✅ | ✅ | Read/Write |
| `prompts/*/settings.yaml` | 🔍 | ✅ | ✅ | Read/Write |
| `prompts/*/notes.md` | 🔍 | ✅ | ✅ | Read/Write |
| `prompts/*/build/*` | ❌ | 🔍 | ✅ | Read/Write |
| `context/` | ✅ | ✅ | ✅ | Read-only |
| `docs/` | ✅ | ✅ | ✅ | Read-only |
| `docs/reference/` | ✅ | ✅ | ✅ | Read-only |

## Constraints and Guardrails

### Universal Constraints
1. **Never modify source tree directly during matching** — use `prompts/<name>/` sandbox only
2. **Always verify with objdiff before integrating** — `run_objdiff` returns pass/fail
3. **Stop on first gate failure** — report diagnostic and do not proceed
4. **No destructive operations without verification** — all integrations blocked until objdiff passes

### Per-Agent Constraints

**ghidra-binary-scout:**
- Exploration only; cannot create or modify decomp work
- Cannot claim matches without verification from other agents
- Must hand off to decomp-prompt-architect or decomp-function-agent

**decomp-prompt-architect:**
- Can create prompts but cannot execute full decompose/integrate workflow
- Can validate prompt structure but cannot run compile/objdiff
- Must notify decomp-function-agent to run full matching

**decomp-function-agent:**
- Cannot integrate without explicit user approval
- Must mark status as "ready_for_integration" before user invokes `/decomp-integrate`
- Must log all objdiff attempts and final verification to notes.md

## Context Injection Template

All agents receive this context block on startup via `inject-context.sh`:

```
## Workspace Context (Injected at Startup)

**Capabilities (this agent):**
- MCP Tools: get_workspace_context, list_prompts, run_objdiff, <agent-specific-mcp>
- Slash Commands: /help, /ghidra-scout, /decomp-prompt, <agent-available>
- File Access: Read/write to prompts/*, context/, docs/
- Verification: objdiff gate via run_objdiff MCP tool

**Current Workspace State (from get_workspace_context):**
- Total prompts: <N>
- Matched: <N>, Integrated: <N>, In-progress: <N>, Blocked: <N>
- Recent activity: <last 3 updates>
- Ghidra servers: <status>

**Constraints:**
- Never modify source tree directly during matching
- Always verify with objdiff before integrating
- Stop on first gate failure; report diagnostic
- See CAPABILITY_MATRIX.md for detailed operation matrix
```

## Verification Checklist

- [ ] CAPABILITY_MATRIX.md exists and is accurate
- [ ] All agents receive context block on startup (no errors)
- [ ] Context injection does not slow agent startup
- [ ] Agent prompts include dynamic workspace state
- [ ] Capability matrix matches actual MCP tool availability
- [ ] All constraints are enforced in agent prompts
- [ ] Agents understand when to hand off vs. proceed

## See Also

- `AGENTS.md` — Agent overview and quick reference
- `.cursor/agents/*/AGENT.md` — Individual agent descriptions
- `scripts/inject-context.sh` — Context injection helper
- `docs/knowledgebase/50-execution/` — Implementation guides
