---
description: Display all available capabilities — agents, commands, MCP tools, and quick reference.
---

Get a complete guide to what you can do in this workspace.

## Output

The `/help` command returns:

1. **Available Agents** — Mizuchi-specialized agents for matching-decompilation tasks
2. **Available Commands** — Slash commands for running agents and operations
3. **Available MCP Tools** — Workspace MCP primitives (get_workspace_context, list_prompts, run_objdiff)
4. **Quick Reference** — Common operations and workflows

## Typical Use

```
/help
```

Displays the full capability matrix and reference guide.

## For Agents

Reference the output in your prompts to understand what operations you can invoke. Example:

> You can invoke commands like `/decomp-function` or `/decomp-prompt` from the help output.
> You can query workspace state via `get_workspace_context()` MCP tool.
> You can list available work via `list_prompts(status=in_progress)` MCP tool.

## For Users

Run `/help` to discover agents, commands, and tools available in this workspace. Use this as your starting point for any matching-decompilation task.
