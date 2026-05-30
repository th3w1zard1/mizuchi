# LFG smoke harness

Run:

```bash
./scripts/lfg-smoke.sh --name demo
./scripts/verify-workspace-surface.sh
```

Expected output:

```text
LFG_SMOKE_OK name=demo
WORKSPACE_SURFACE_OK
```

Invalid or missing arguments return exit code `2` with usage text.
