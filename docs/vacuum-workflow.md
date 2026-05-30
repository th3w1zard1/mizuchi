# Vacuum Workflow

Use the autonomous loop to process prompt folders in score order with persistent queue state.

## Start

```bash
./scripts/vacuum-cli.sh start
```

## Resume

```bash
./scripts/vacuum-cli.sh resume
```

## Monitor

```bash
./scripts/vacuum-cli.sh status
./scripts/vacuum-cli.sh inspect-queue
tail -f logs/progress.log
```

## Retry a difficult function

```bash
./scripts/vacuum-cli.sh reset-queue --function fun_00148020
```
