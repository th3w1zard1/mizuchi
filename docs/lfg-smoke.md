# LFG smoke harness

Run:

```bash
./scripts/lfg-smoke.sh --name demo
```

Expected output:

```text
LFG_SMOKE_OK name=demo
WORKSPACE_SURFACE_OK
PROMPT_STATUS_OK
```

The smoke script chains workspace surface verification and prompt status integrity checks.

Invalid or missing arguments return exit code `2` with usage text.
