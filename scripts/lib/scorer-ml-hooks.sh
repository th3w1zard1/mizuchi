#!/usr/bin/env bash
# Interface reserved for future trained scoring. Defaults to deterministic heuristic.
set -euo pipefail

scorer_ml_enabled() {
  [[ "${SCORER_ML_ENABLED:-false}" == "true" ]]
}

scorer_ml_metadata_json() {
  jq -n \
    --argjson enabled "$(scorer_ml_enabled && printf 'true' || printf 'false')" \
    --arg model "${SCORER_ML_MODEL:-}" \
    '{
      enabled: $enabled,
      model: (if $model == "" then null else $model end),
      fallback: "heuristic"
    }'
}

scorer_ml_predict_prompt_json() {
  local prompt_dir="$1" name="${2:-}"
  # ML scoring is intentionally not implemented in this cycle; callers get the
  # same stable heuristic shape while the interface remains swappable.
  scorer_score_prompt_json "$prompt_dir" "$name" | jq '. + {scorer: "heuristic"}'
}
