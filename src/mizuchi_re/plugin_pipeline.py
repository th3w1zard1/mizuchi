"""Upstream-style plugin pipeline orchestration for Python recovery flows.

This ports the core Mizuchi lifecycle from the TypeScript upstream:
setup phase, staged programmatic phase, retrying main phase, and post-match
phase. Plugins exchange a mutable context and emit structured results so
compiler/objdiff/source-generation steps can be composed without hardcoding a
single monolithic recovery loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


PluginStatus = str
PipelineEventHandler = Callable[[dict[str, Any]], None]


@dataclass
class PluginResult:
    plugin_id: str
    plugin_name: str
    status: PluginStatus
    duration_ms: int
    error: str | None = None
    output: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    sections: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "pluginId": self.plugin_id,
            "pluginName": self.plugin_name,
            "status": self.status,
            "durationMs": self.duration_ms,
            "data": self.data,
        }
        if self.error:
            row["error"] = self.error
        if self.output:
            row["output"] = self.output
        if self.sections:
            row["sections"] = self.sections
        return row


@dataclass
class AttemptResult:
    attempt_number: int
    plugin_results: list[PluginResult]
    success: bool
    duration_ms: int
    start_timestamp_ms: int

    def to_json(self) -> dict[str, Any]:
        return {
            "attemptNumber": self.attempt_number,
            "pluginResults": [result.to_json() for result in self.plugin_results],
            "success": self.success,
            "durationMs": self.duration_ms,
            "startTimestampMs": self.start_timestamp_ms,
        }


@dataclass
class PipelineRunResult:
    prompt_path: str
    function_name: str
    success: bool
    attempts: list[AttemptResult]
    total_duration_ms: int
    setup_phase: AttemptResult | None = None
    programmatic_phase: AttemptResult | None = None
    post_match_phase: AttemptResult | None = None
    match_source: str | None = None

    def to_json(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "promptPath": self.prompt_path,
            "functionName": self.function_name,
            "success": self.success,
            "attempts": [attempt.to_json() for attempt in self.attempts],
            "totalDurationMs": self.total_duration_ms,
        }
        if self.setup_phase is not None:
            row["setupPhase"] = self.setup_phase.to_json()
        if self.programmatic_phase is not None:
            row["programmaticPhase"] = self.programmatic_phase.to_json()
        if self.post_match_phase is not None:
            row["postMatchPhase"] = self.post_match_phase.to_json()
        if self.match_source:
            row["matchSource"] = self.match_source
        return row


class Plugin(Protocol):
    id: str
    name: str
    description: str

    def execute(self, context: dict[str, Any]) -> tuple[PluginResult, dict[str, Any]]:
        """Run the plugin and return a result plus updated context."""

    def prepare_retry(
        self,
        context: dict[str, Any],
        previous_attempts: list[dict[str, PluginResult]],
    ) -> dict[str, Any]:
        """Optionally mutate context before the next retry."""


class PluginPipeline:
    def __init__(self, *, max_retries: int = 3, event_handler: PipelineEventHandler | None = None) -> None:
        self.max_retries = max(1, max_retries)
        self.event_handler = event_handler
        self.setup_phase_plugins: list[Plugin] = []
        self.programmatic_phase_stages: list[list[Plugin]] = []
        self.plugins: list[Plugin] = []
        self.post_match_plugins: list[Plugin] = []

    def register(self, *plugins: Plugin) -> "PluginPipeline":
        self.plugins.extend(plugins)
        self._emit_registered(plugins)
        return self

    def register_setup_phase(self, *plugins: Plugin) -> "PluginPipeline":
        self.setup_phase_plugins.extend(plugins)
        self._emit_registered(plugins)
        return self

    def register_programmatic_phase(self, *stages: list[Plugin]) -> "PluginPipeline":
        self.programmatic_phase_stages.extend(stages)
        for stage in stages:
            self._emit_registered(stage)
        return self

    def register_post_match_phase(self, *plugins: Plugin) -> "PluginPipeline":
        self.post_match_plugins.extend(plugins)
        self._emit_registered(plugins)
        return self

    def run_pipeline(
        self,
        *,
        prompt_path: str,
        prompt_content: str,
        function_name: str,
        target_object_path: str,
        asm: str,
        config: dict[str, Any] | None = None,
        initial_context: dict[str, Any] | None = None,
    ) -> PipelineRunResult:
        start = now_ms()
        context: dict[str, Any] = {
            "promptPath": prompt_path,
            "promptContent": prompt_content,
            "functionName": function_name,
            "targetObjectPath": target_object_path,
            "asm": asm,
            "attemptNumber": 1,
            "maxRetries": self.max_retries,
            "previousAttempts": [],
            "config": config or {},
        }
        if initial_context:
            context.update(initial_context)

        setup_phase = None
        if self.setup_phase_plugins:
            self.emit({"type": "setup-phase-start"})
            setup_phase, context = self.run_attempt(context, self.setup_phase_plugins)
            if not setup_phase.success:
                return PipelineRunResult(
                    prompt_path=prompt_path,
                    function_name=function_name,
                    success=False,
                    attempts=[],
                    total_duration_ms=now_ms() - start,
                    setup_phase=setup_phase,
                )

        programmatic_phase = None
        if self.programmatic_phase_stages:
            self.emit({"type": "programmatic-phase-start"})
            all_results: list[PluginResult] = []
            stage_start = now_ms()
            stage_context = context
            stage_success = False
            for stage in self.programmatic_phase_stages:
                attempt, stage_context = self.run_attempt(stage_context, stage)
                all_results.extend(attempt.plugin_results)
                if attempt.success:
                    stage_success = True
                    break
            programmatic_phase = AttemptResult(
                attempt_number=int(context.get("attemptNumber") or 1),
                plugin_results=all_results,
                success=stage_success,
                duration_ms=now_ms() - stage_start,
                start_timestamp_ms=stage_start,
            )
            context = stage_context
            if stage_success:
                post_match_phase = self.run_post_match(context)
                return PipelineRunResult(
                    prompt_path=prompt_path,
                    function_name=function_name,
                    success=True,
                    attempts=[],
                    total_duration_ms=now_ms() - start,
                    setup_phase=setup_phase,
                    programmatic_phase=programmatic_phase,
                    post_match_phase=post_match_phase,
                    match_source="programmatic-phase",
                )

        attempts: list[AttemptResult] = []
        previous_attempts: list[dict[str, PluginResult]] = []
        success = False
        for attempt_number in range(1, self.max_retries + 1):
            context["attemptNumber"] = attempt_number
            self.emit({"type": "attempt-start", "attemptNumber": attempt_number, "maxRetries": self.max_retries})
            attempt, context = self.run_attempt(context, self.plugins)
            attempts.append(attempt)
            will_retry = not attempt.success and attempt_number < self.max_retries
            self.emit(
                {
                    "type": "attempt-complete",
                    "attemptNumber": attempt_number,
                    "success": attempt.success,
                    "willRetry": will_retry,
                    "differenceCount": best_difference_count(attempt),
                }
            )
            if attempt.success:
                success = True
                break
            if will_retry:
                attempt_map = {result.plugin_id: result for result in attempt.plugin_results if result.status != "skipped"}
                previous_attempts.append(attempt_map)
                context["previousAttempts"] = previous_attempts
                context = self.prepare_retry(context, previous_attempts)

        post_match_phase = self.run_post_match(context) if success else None
        return PipelineRunResult(
            prompt_path=prompt_path,
            function_name=function_name,
            success=success,
            attempts=attempts,
            total_duration_ms=now_ms() - start,
            setup_phase=setup_phase,
            programmatic_phase=programmatic_phase,
            post_match_phase=post_match_phase,
            match_source="main-phase" if success else None,
        )

    def run_attempt(self, context: dict[str, Any], plugins: list[Plugin]) -> tuple[AttemptResult, dict[str, Any]]:
        start = now_ms()
        current_context = dict(context)
        results: list[PluginResult] = []
        success = True
        should_stop = False
        for plugin in plugins:
            if should_stop:
                result = PluginResult(plugin.id, plugin.name, "skipped", 0, output="Skipped due to previous plugin failure")
                results.append(result)
                self.emit_complete(plugin, result)
                continue
            self.emit({"type": "plugin-execution-start", "pluginId": plugin.id, "pluginName": plugin.name})
            plugin_start = now_ms()
            try:
                result, current_context = plugin.execute(current_context)
            except Exception as exc:  # noqa: BLE001 - plugin boundary must isolate failures.
                result = PluginResult(
                    plugin.id,
                    plugin.name,
                    "failure",
                    now_ms() - plugin_start,
                    error=f"Unexpected error: {exc}",
                )
            results.append(result)
            self.emit_complete(plugin, result)
            if result.status == "failure":
                success = False
                should_stop = True
        return (
            AttemptResult(
                attempt_number=int(context.get("attemptNumber") or 1),
                plugin_results=results,
                success=success,
                duration_ms=now_ms() - start,
                start_timestamp_ms=start,
            ),
            current_context,
        )

    def prepare_retry(self, context: dict[str, Any], previous_attempts: list[dict[str, PluginResult]]) -> dict[str, Any]:
        updated = dict(context)
        for plugin in self.plugins:
            prepare = getattr(plugin, "prepare_retry", None)
            if prepare is not None:
                updated = prepare(updated, previous_attempts)
        return updated

    def run_post_match(self, context: dict[str, Any]) -> AttemptResult | None:
        if not self.post_match_plugins:
            return None
        self.emit({"type": "post-match-phase-start"})
        result, _context = self.run_attempt(context, self.post_match_plugins)
        return result

    def emit_complete(self, plugin: Plugin, result: PluginResult) -> None:
        self.emit(
            {
                "type": "plugin-execution-complete",
                "pluginId": plugin.id,
                "pluginName": plugin.name,
                "status": result.status,
                "error": result.error,
                "durationMs": result.duration_ms,
            }
        )

    def emit(self, event: dict[str, Any]) -> None:
        if self.event_handler is not None:
            self.event_handler(event)

    def _emit_registered(self, plugins: list[Plugin] | tuple[Plugin, ...]) -> None:
        for plugin in plugins:
            self.emit(
                {
                    "type": "plugin-registered",
                    "plugin": {"id": plugin.id, "name": plugin.name, "description": plugin.description},
                }
            )


def now_ms() -> int:
    return int(time.time() * 1000)


def best_difference_count(attempt: AttemptResult) -> int | None:
    counts = []
    for result in attempt.plugin_results:
        value = result.data.get("differenceCount") if isinstance(result.data, dict) else None
        if isinstance(value, int):
            counts.append(value)
    return min(counts) if counts else None
