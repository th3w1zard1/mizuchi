---
description: Display all available capabilities — agents, commands, MCP tools, and quick reference.
---

Get a complete guide to what you can do in this workspace.

## Output

The `/help` command returns:

1. **Primary Entrypoint** — `./scripts/decomp-cli.sh` as the canonical shell/runtime surface
2. **Available Agents** — Mizuchi-specialized agents for reverse-engineering tasks
3. **Available Commands** — Slash-command parity surface
4. **Available MCP Tools** — Workspace MCP primitives (get_workspace_context, list_prompts, run_objdiff)
5. **Quick Reference** — Common operations and workflows

## Typical Use

```
/help
```

Displays the full capability matrix and reference guide.

## For Agents

Reference the output in your prompts to understand what operations you can invoke. Example:

> The primary shell surface is `./scripts/decomp-cli.sh`.
> You can also invoke commands like `/decomp-function` or `/ghidra-scout` from the help output.
> You can query workspace state via `get_workspace_context()` MCP tool.
> You can list available work via `list_prompts(status=in_progress)` MCP tool.

## For Users

Run `/help` to discover the primary entrypoint, agents, commands, and tools available in this workspace. Use this as your starting point for any Mizuchi task.
