---
title: "feat: Implement agent-native foundation (Priority 1)"
status: active
type: feat
created: 2026-05-29
origin: none
---

## Summary

Implement 3 MCP primitives, 1 discovery command, and 1 documentation artifact to move ReconstructKit from 43% agent-native to 75%+ agent-native. Agents can query workspace state, discover capabilities, and understand what they can do without being told.

## Problem Frame

Agent-native audit identified critical gaps: agents cannot discover their capabilities, cannot query workspace state dynamically, and tools are orchestration-heavy rather than primitives. This blocks true autonomous agent operation in the matching-decompilation workflow.

## Scope Boundaries

### In Scope

- Add 3 MCP primitive tools: `get_workspace_context()`, `list_prompts()`, `run_objdiff()`.
- Implement `/help` command listing all agent capabilities.
- Add agent self-descriptions to all `.cursor/agents/` files.
- Create `CAPABILITY_MATRIX.md` documenting all agent-accessible operations.
- Add context injection to agent prompts so they include workspace awareness on startup.

### Out of Scope

- Refactoring skills into true primitives (deferred to Priority 3).
- UI streaming or real-time updates (deferred to Priority 4).
- Config-driven validation schema/objdiff patterns (deferred to Priority 3).

### Deferred to Follow-Up Work

- MCP tool performance optimization.
- Agent autonomy grading/scoring system.
- Distributed agent coordination across multiple decomp projects.

## Key Technical Decisions

- Use MCP for all new primitives; no shell wrappers for agent-facing operations.
- Context injection happens at agent prompt load time, not on every invocation.
- `/help` command is synchronous and returns structured capability list.
- Capability matrix is a markdown artifact, not a runtime database.

## System-Wide Impact

- **Agents:** Gain full workspace visibility, can self-discover operations, understand constraints without prose documentation.
- **Users:** Reduced friction in telling agents what to do; agents can ask for help and get structured guidance.
- **MCP wiring:** 3 new tools (get_workspace_context, list_prompts, run_objdiff) added to `.cursor/mcp.json`; no breaking changes to existing tools.
- **Prompts:** Agent prompts become context-aware; all agents receive dynamic workspace summary on startup.

## Implementation Units

### U1. Add MCP primitive: get_workspace_context()

**Goal:** Return a structured summary of workspace state so agents can understand the environment at startup.

**Requirements:** Agents can discover current state without querying multiple sources.

**Dependencies:** None.

**Files:**
- `.cursor/mcp.json` (add tool definition)
- scripts/get-workspace-context.sh (implement tool)
- tests/test-get-workspace-context.sh (test tool)

**Approach:**

Implement MCP tool that returns JSON with:
- `prompt_queue`: list of prompt folders, their status (matched/integrated/in-progress), recent activity
- `ghidra_status`: connected servers, loaded programs, active analysis state (if available via MCP)
- `build_artifacts`: recent compiled outputs, objdiff results
- `active_branches`: current git branch, remotes, unpushed commits
- `workspace_metrics`: total prompts, match rate, integration rate

Tool executes without arguments; returns JSON immediately.

**Patterns to follow:**

- Existing `scripts/decomp-cli.sh` pattern for CLI entrypoints.
- Use `jq` for JSON generation.
- Cache-friendly (no expensive file scanning per call; use mtime checks).

**Test scenarios:**

- Happy path: returns valid JSON with all required fields.
- Workspace state accuracy: counts match actual filesystem state.
- Performance: executes in <1 second even with 100+ prompts.

**Verification:**

- MCP tool callable from agent prompt without error.
- Agent receives structured context on first message.

---

### U2. Add MCP primitive: list_prompts()

**Goal:** Agents can query available prompt folders and their metadata without filesystem traversal.

**Requirements:** Agents discover what decomp work is queued/in-progress/done.

**Dependencies:** None.

**Files:**
- `.cursor/mcp.json` (add tool definition)
- scripts/list-prompts.sh (implement tool)
- tests/test-list-prompts.sh (test tool)

**Approach:**

Implement MCP tool that returns list of prompts as JSON array:
```json
{
  "prompts": [
    { "name": "fun_00148020", "status": "matched", "function_name": "xyz", "last_updated": "2026-05-29" },
    { "name": "fun_0014a050", "status": "in_progress", "function_name": "abc", "last_updated": "2026-05-28" }
  ]
}
```

Tool accepts optional filter: `status=matched` or `status=integrated` to narrow results.

**Patterns to follow:**

- Parse `prompts/*/prompt.md` and `prompts/*/notes.md` for metadata.
- Fallback to filename if notes.md doesn't exist.

**Test scenarios:**

- Happy path: returns all prompts with accurate status.
- Filter by status: `list_prompts(status=matched)` returns only matched prompts.
- Empty queue: returns empty array if no prompts exist.

**Verification:**

- Agent can list available work without filesystem knowledge.
- Status field matches `prompts/*/notes.md` content.

---

### U3. Add MCP primitive: run_objdiff()

**Goal:** Agents can validate matches programmatically without shell knowledge.

**Requirements:** Agents can invoke objdiff gate before integrating.

**Dependencies:** U1, U2 (agents understand when to validate).

**Files:**
- `.cursor/mcp.json` (add tool definition)
- scripts/run-objdiff.sh (wrapper around decomp-cli.sh verify)
- tests/test-run-objdiff.sh (test tool)

**Approach:**

Implement MCP tool that wraps existing `scripts/decomp-cli.sh verify-surface` and returns success/failure:
```json
{
  "status": "matched",
  "differences": 0,
  "message": "WORKSPACE_SURFACE_OK"
}
```

Tool accepts arguments: `target.o` and `candidate.o` file paths (repo-relative).

**Patterns to follow:**

- Reuse existing objdiff-gate.sh logic; no new verification logic.
- Return structured result (not just exit code).

**Test scenarios:**

- Happy path: objdiff returns 0 differences, tool returns `{"status": "matched", "differences": 0}`.
- Mismatch case: objdiff returns N>0 differences, tool returns `{"status": "mismatched", "differences": N}`.

**Verification:**

- Agent receives clear pass/fail signal without parsing shell output.
- Integration proceeds only after objdiff returns `differences: 0`.

---

### U4. Add `/help` command and agent self-descriptions

**Goal:** Agents and users can discover what capabilities are available.

**Requirements:** Capability discovery without documentation hunting.

**Dependencies:** U1, U2, U3.

**Files:**
- `.cursor/commands/help.md` (create /help slash command)
- `.cursor/agents/ghidra-binary-scout/AGENT.md` (add self-description)
- `.cursor/agents/decomp-prompt-architect/AGENT.md` (add self-description)
- `.cursor/agents/decomp-function-agent/AGENT.md` (add self-description)
- scripts/help-command.sh (implement tool backend)
- tests/test-help-command.sh (test command)

**Approach:**

Create `/help` command that returns:
1. List of all skills available (ce-plan, ce-work, ce-code-review, etc.)
2. List of all agents available (ghidra-scout, decomp-prompt-architect, etc.)
3. List of all MCP tools available
4. Quick reference for most common operations

Add to each agent's AGENT.md frontmatter:
```yaml
capabilities:
  - "Can invoke: /ghidra-scout, /decomp-prompt, /decomp-function"
  - "Can read: prompts/*, context/, docs/"
  - "Can write: prompts/*/prompt.md, prompts/*/notes.md"
  - "Can execute: compile, verify-surface, objdiff"
```

**Patterns to follow:**

- Sync with `.cursor/commands/` directory listing.
- Parse skill metadata from SKILL.md files.

**Test scenarios:**

- `/help` returns non-empty structured output.
- Each agent's AGENT.md includes capabilities section.
- Help output matches actual available operations.

**Verification:**

- User or agent can run `/help` and receive complete, accurate capability list.
- Agent can reference its own capabilities from prompt.

---

### U5. Create CAPABILITY_MATRIX.md and context injection

**Goal:** Centralized documentation of all agent-accessible operations; agents start with full context.

**Requirements:** Single source of truth for agent capabilities; agents understand constraints at prompt startup.

**Dependencies:** U1, U2, U3, U4.

**Files:**
- `CAPABILITY_MATRIX.md` (create; matrix of agents × operations)
- `.cursor/agents/*/AGENT.md` (update all agents with context injection instructions)
- scripts/inject-context.sh (helper to build context block for agents)

**Approach:**

Create `CAPABILITY_MATRIX.md` as a table:

| Agent | Can Scout | Can Prompt | Can Decompose | Can Integrate | Can Verify | Can Update State |
|-------|-----------|-----------|---------------|---------------|-----------|------------------|
| ghidra-binary-scout | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| decomp-prompt-architect | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| decomp-function-agent | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |

Add context injection to each agent's system prompt template:

```
## Workspace Context
You have access to the following:

**Capabilities:**
- MCP Tools: get_workspace_context, list_prompts, run_objdiff
- Slash Commands: /help, /ghidra-scout, /decomp-prompt, /decomp-function, /decomp-integrate
- File Access: read/write to prompts/*, context/, docs/
- Verification: objdiff gate via run_objdiff MCP tool

**Current Workspace State:** [injected by get_workspace_context at prompt startup]

**Constraints:**
- Never modify source tree directly during matching
- Always verify with objdiff before integrating
- Stop on first gate failure; report diagnostic
```

**Patterns to follow:**

- Reuse existing AGENTS.md for template.
- Context injection happens via prompt processor (agent startup, not per-invocation).

**Test scenarios:**

- CAPABILITY_MATRIX.md matches actual tool availability.
- Agents receive context block without error on startup.
- Context injection does not slow agent startup (cache-friendly).

**Verification:**

- Agent prompts include dynamic context on first message.
- CAPABILITY_MATRIX.md is accurate and up-to-date.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|-----------|
| MCP tool performance degradation with large prompt queues | Cache workspace state in memory; invalidate on file changes. Start with filesystem scan; optimize if needed. |
| Agent prompts become too large with context injection | Inject only essential context (queue summary, recent activity). Defer detailed state to lazy queries via MCP tools. |
| Stale context if files change during agent operation | Agents can refresh context via `get_workspace_context()` call. No guarantees of strict consistency. |

## Deferred Implementation Notes

- Exact MCP tool performance benchmarks (measure after implementation).
- Agent autonomy grading system (out of scope for Priority 1).
- UI integration for help command (Priority 4).

## Verification

The unit is complete when:
- All 3 MCP tools (get_workspace_context, list_prompts, run_objdiff) are defined in `.cursor/mcp.json` and callable without error.
- `/help` command returns structured, accurate capability list.
- All agent AGENT.md files include `capabilities:` frontmatter.
- `CAPABILITY_MATRIX.md` exists and matches actual tool availability.
- Agent prompts receive dynamic context injection on startup.
- Agent-native architecture audit score increases from 43% to 75%+.
