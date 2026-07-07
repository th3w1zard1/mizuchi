#!/usr/bin/env bash
# Queue schema constants for the autonomous matching loop.
set -euo pipefail

QUEUE_SCHEMA_VERSION="reconkit.vacuum-queue.v1"
QUEUE_DEFAULT_PATH="state/queue.json"
SCORES_DEFAULT_PATH="state/scores.json"
VACUUM_SESSION_DEFAULT_PATH="state/vacuum-session.json"
VACUUM_PROGRESS_DEFAULT_PATH="logs/vacuum-progress.log"

# Runtime queue states (arrays in queue.json).
QUEUE_STATES=(pending matched integrated failed difficult)

# Maximum autonomous retry attempts before marking difficult.
QUEUE_DEFAULT_MAX_ATTEMPTS=10
