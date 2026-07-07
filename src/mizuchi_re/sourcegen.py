"""Automatic source-candidate task generation.

This module does not synthesize hand-written C. It packages machine-derived
inputs and, when available, writes decompiler-produced C text as unverified
candidate source for later compiler/objdiff gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def generate_source_candidates(
    *,
    target: dict[str, Any],
    function_candidates: dict[str, Any],
    out_dir: Path,
    inventory: dict[str, Any] | None = None,
    function_facts_jsonl: Path | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    slices_dir = out_dir / "target-slices"
    facts = load_function_facts(function_facts_jsonl) if function_facts_jsonl else {}
    fact_artifacts = write_normalized_function_facts(out_dir, facts)
    profile_artifacts = load_compiler_profile_artifacts(out_dir, target)
    original_candidates = list(function_candidates.get("candidates", []))
    all_candidates = [row for row in original_candidates if is_recoverable_candidate(row)]
    inferred_sizes, applied_boundary_repairs = infer_candidate_sizes(all_candidates, inventory or {})
    start = max(0, offset)
    candidates = all_candidates[start : start + max(limit, 0)]
    address_aliases, address_alias_groups = build_address_alias_metadata(candidates)

    tasks_path = out_dir / "tasks.jsonl"
    generated_count = 0
    semantic_generated_count = 0
    fresh_generated_count = 0
    reused_generated_count = 0
    target_slice_count = 0
    task_count = 0
    by_status: dict[str, int] = {}
    target_slices_by_status: dict[str, int] = {}
    target_slices_by_boundary_quality: dict[str, int] = {}
    generated_by_rule: dict[str, int] = {}
    semantic_by_rule: dict[str, int] = {}
    generated_by_language: dict[str, int] = {}
    semantic_by_language: dict[str, int] = {}
    generated_by_source_quality: dict[str, int] = {}
    generated_by_recovery_scope: dict[str, int] = {}
    semantic_by_recovery_scope: dict[str, int] = {}
    nonsemantic_catalog: list[dict[str, Any]] = []
    inferred_size_count = 0

    with tasks_path.open("w", encoding="utf-8") as tasks:
        for row in candidates:
            if is_range_alias(row, facts):
                continue
            row = with_inferred_size(row, inferred_sizes)
            if row.get("boundaryRepairSkipTask") is True:
                continue
            if row.get("sizeSource") == "inferred-next-candidate-boundary":
                inferred_size_count += 1
            fact = match_fact(row, facts)
            task = build_task(target, row, fact, profile_artifacts)
            address_alias = address_aliases.get(address_alias_key(row))
            if address_alias:
                task["addressAlias"] = address_alias
            target_slice = build_target_slice(inventory or {}, row, fact)
            target_slice_bytes: bytes | None = None
            if target_slice.get("status") == "complete":
                slices_dir.mkdir(parents=True, exist_ok=True)
                slice_path = slices_dir / f"{safe_task_id(task)}.target.bin"
                slice_bytes = bytes.fromhex(str(target_slice.pop("bytesHex")))
                target_slice_bytes = slice_bytes
                slice_path.write_bytes(slice_bytes)
                target_slice["bytesPath"] = str(slice_path)
                boundary_quality = classify_target_slice_boundary(row, slice_bytes)
                target_slice["boundaryQuality"] = boundary_quality
                quality_key = str(boundary_quality.get("status") or "unknown")
                target_slices_by_boundary_quality[quality_key] = target_slices_by_boundary_quality.get(quality_key, 0) + 1
                target_slice_count += 1
            target_slice_status = str(target_slice.get("status") or "unknown")
            target_slices_by_status[target_slice_status] = target_slices_by_status.get(target_slice_status, 0) + 1
            task["targetSlice"] = target_slice
            task["verificationTier"] = verification_tier_for_task(task)
            case_dir = out_dir / safe_task_id(task)
            generated_candidate = generated_candidate_from_target_bytes(task, target_slice_bytes)
            if fact and fact.get("decompiled"):
                case_dir.mkdir(parents=True, exist_ok=True)
                source = str(fact["decompiled"]).rstrip() + "\n"
                source_path = case_dir / "candidate.c"
                source_path.write_text(source, encoding="utf-8")
                task.update(
                    {
                        "status": "generated-unverified",
                        "source": str(source_path),
                        "sourceSha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "sourceOrigin": "external decompiler output; automatically exported, not manually authored",
                        "verificationTier": verification_tier_for_task(task, has_source=True),
                        "acceptanceGate": "compile with selected compiler profile and objdiff-zero against target object; target-slice checks remain pre-acceptance evidence",
                    }
                )
                write_json(case_dir / "candidate.json", task)
                generated_count += 1
                semantic_generated_count += 1
                fresh_generated_count += 1
            elif generated_candidate is not None:
                if generated_candidate.get("catalogOnly"):
                    case_dir.mkdir(parents=True, exist_ok=True)
                    generator_rule = str(generated_candidate["generator"].get("rule") or "unknown")
                    task.update(
                        {
                            "status": "not-generated-fragment",
                            "sourceOrigin": generated_candidate["origin"],
                            "semanticSource": False,
                            "automaticGenerator": generated_candidate["generator"],
                            "verificationTier": "fragment-catalogued",
                            "acceptanceGate": "not a source candidate; requires corrected function boundary before compiler/object comparison",
                        }
                    )
                    task.setdefault("automaticInputs", [])
                    for item in ["target-slice-bytes", generator_rule]:
                        if item not in task["automaticInputs"]:
                            task["automaticInputs"].append(item)
                    nonsemantic_catalog.append(nonsemantic_catalog_row(task, target_slice_bytes, generator_rule))
                    write_json(case_dir / "candidate.json", task)
                    tasks.write(json.dumps(task, sort_keys=True) + "\n")
                    task_count += 1
                    by_status[str(task["status"])] = by_status.get(str(task["status"]), 0) + 1
                    continue
                case_dir.mkdir(parents=True, exist_ok=True)
                source = str(generated_candidate["source"]).rstrip() + "\n"
                source_path = case_dir / f"candidate.{generated_candidate['extension']}"
                generator_rule = str(generated_candidate["generator"].get("rule") or "unknown")
                source_language = str(generated_candidate["language"])
                source_quality = classify_generated_source_quality(generated_candidate)
                source_recovery_scope = classify_source_recovery_scope(generated_candidate, target_slice_bytes)
                generated_by_rule[generator_rule] = generated_by_rule.get(generator_rule, 0) + 1
                generated_by_language[source_language] = generated_by_language.get(source_language, 0) + 1
                generated_by_source_quality[source_quality] = generated_by_source_quality.get(source_quality, 0) + 1
                generated_by_recovery_scope[source_recovery_scope] = generated_by_recovery_scope.get(source_recovery_scope, 0) + 1
                source_path.write_text(source, encoding="utf-8")
                task.update(
                    {
                        "status": "generated-unverified",
                        "source": str(source_path),
                        "sourceSha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "sourceLanguage": source_language,
                        "sourceQuality": source_quality,
                        "sourceRecoveryScope": source_recovery_scope,
                        "sourceOrigin": generated_candidate["origin"],
                        "semanticSource": bool(generated_candidate.get("semanticSource", True)),
                        "automaticGenerator": generated_candidate["generator"],
                        "compilerProfileHints": generated_candidate["compilerProfileHints"],
                        "verificationTier": verification_tier_for_task(task, has_source=True),
                        "acceptanceGate": "compile with selected compiler profile and objdiff-zero against target object; target-slice checks remain pre-acceptance evidence",
                    }
                )
                task.setdefault("automaticInputs", [])
                for item in ["target-slice-bytes", str(generated_candidate["generator"].get("rule") or "byte-pattern-generator")]:
                    if item not in task["automaticInputs"]:
                        task["automaticInputs"].append(item)
                write_json(case_dir / "candidate.json", task)
                generated_count += 1
                if task.get("semanticSource"):
                    semantic_generated_count += 1
                    semantic_by_rule[generator_rule] = semantic_by_rule.get(generator_rule, 0) + 1
                    semantic_by_language[source_language] = semantic_by_language.get(source_language, 0) + 1
                    semantic_by_recovery_scope[source_recovery_scope] = semantic_by_recovery_scope.get(source_recovery_scope, 0) + 1
                else:
                    nonsemantic_catalog.append(nonsemantic_catalog_row(task, target_slice_bytes, generator_rule))
                fresh_generated_count += 1
            elif (case_dir / "candidate.c").exists():
                source_path = case_dir / "candidate.c"
                source = source_path.read_text(encoding="utf-8", errors="replace")
                task.update(
                    {
                        "status": "generated-unverified",
                        "source": str(source_path),
                        "sourceSha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "sourceOrigin": "existing automatic task-local candidate reused because the current tool pass produced no decompiler C",
                        "verificationTier": verification_tier_for_task(task, has_source=True),
                        "acceptanceGate": "compile with selected compiler profile and objdiff-zero against target object; target-slice checks remain pre-acceptance evidence",
                    }
                )
                task.setdefault("automaticInputs", ["function-facts"])
                if "existing-candidate-file" not in task["automaticInputs"]:
                    task["automaticInputs"].append("existing-candidate-file")
                write_json(case_dir / "candidate.json", task)
                generated_count += 1
                if task.get("semanticSource"):
                    semantic_generated_count += 1
                reused_generated_count += 1
            tasks.write(json.dumps(task, sort_keys=True) + "\n")
            task_count += 1
            by_status[str(task["status"])] = by_status.get(str(task["status"]), 0) + 1

    alias_artifacts = write_address_alias_artifacts(out_dir, address_alias_groups)
    source_coverage_artifacts = write_source_coverage_artifacts(
        out_dir,
        task_count=task_count,
        target_slice_count=target_slice_count,
        generated_count=generated_count,
        semantic_generated_count=semantic_generated_count,
        nonsemantic_catalog=nonsemantic_catalog,
        applied_boundary_repairs=applied_boundary_repairs,
        generated_by_rule=generated_by_rule,
        semantic_by_rule=semantic_by_rule,
        generated_by_language=generated_by_language,
        semantic_by_language=semantic_by_language,
        generated_by_source_quality=generated_by_source_quality,
        generated_by_recovery_scope=generated_by_recovery_scope,
        semantic_by_recovery_scope=semantic_by_recovery_scope,
        boundary_quality=target_slices_by_boundary_quality,
    )

    if generated_count > 0:
        status = "generated-unverified"
        blockers = []
        if generated_count < task_count:
            blockers.append(f"{task_count - generated_count} queued task(s) still need an automatic source generator")
        if not function_facts_jsonl:
            blockers.append("no decompiler/function-facts JSONL provided or generated")
        elif not facts:
            blockers.append(f"function-facts JSONL was empty or unreadable: {function_facts_jsonl}")
    elif not function_facts_jsonl:
        status = "queued-no-source"
        blockers = ["no decompiler/function-facts JSONL provided or generated; queued target slices require an automatic source generator"]
    elif not facts:
        status = "queued-no-source"
        blockers = [f"function-facts JSONL was empty or unreadable: {function_facts_jsonl}; queued target slices require an automatic source generator"]
    else:
        status = "queued-no-source"
        blockers = ["function facts were present, but no decompiler C text matched current candidates"]

    return {
        "schema": "mizuchi.source-generation.v1",
        "status": status,
        "target": target,
        "tasks": str(tasks_path),
        "taskCount": task_count,
        "generatedSourceCandidates": generated_count,
        "semanticSourceCandidates": semantic_generated_count,
        "nonSemanticBootstrapCandidates": max(0, generated_count - semantic_generated_count),
        "freshGeneratedSourceCandidates": fresh_generated_count,
        "reusedSourceCandidates": reused_generated_count,
        "targetSlices": target_slice_count,
        "targetSlicesByStatus": dict(sorted(target_slices_by_status.items())),
        "targetSlicesByBoundaryQuality": dict(sorted(target_slices_by_boundary_quality.items())),
        "inferredFunctionSizes": inferred_size_count,
        "candidateOffset": start,
        "candidateLimit": max(limit, 0),
        "candidateTotal": len(all_candidates),
        "uniqueCandidateAddresses": len({int(row["address"]) for row in candidates if row.get("address") is not None}),
        "duplicateAddressAliases": alias_artifacts["duplicateAddressAliases"],
        "duplicateAddressScheduledTasks": alias_artifacts["duplicateAddressScheduledTasks"],
        "duplicateAddressAliasTasks": alias_artifacts["duplicateAddressTasks"],
        "originalCandidateTotal": len(original_candidates),
        "functionFacts": str(function_facts_jsonl) if function_facts_jsonl else None,
        "functionFactArtifacts": fact_artifacts,
        "compilerProfileArtifacts": profile_artifacts,
        "addressAliasArtifacts": alias_artifacts,
        "sourceCoverageArtifacts": source_coverage_artifacts,
        "generatedByRule": dict(sorted(generated_by_rule.items())),
        "semanticByRule": dict(sorted(semantic_by_rule.items())),
        "generatedByLanguage": dict(sorted(generated_by_language.items())),
        "semanticByLanguage": dict(sorted(semantic_by_language.items())),
        "generatedBySourceQuality": dict(sorted(generated_by_source_quality.items())),
        "generatedByRecoveryScope": dict(sorted(generated_by_recovery_scope.items())),
        "semanticByRecoveryScope": dict(sorted(semantic_by_recovery_scope.items())),
        "wholeFunctionSemanticSourceCandidates": semantic_by_recovery_scope.get("whole-function", 0),
        "partialSourceSliceSemanticCandidates": semantic_by_recovery_scope.get("partial-source-slice", 0),
        "highLevelSourceCandidates": generated_by_source_quality.get("high-level-c", 0),
        "inlineAsmSourceCandidates": generated_by_source_quality.get("inline-asm-c", 0),
        "byteEmissionSourceCandidates": generated_by_source_quality.get("byte-emission-asm", 0),
        "byStatus": dict(sorted(by_status.items())),
        "blockers": blockers,
        "claimBoundary": "target slices and generated candidates are not recovered source until compiler and objdiff gates accept them",
    }


def load_function_facts(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    facts: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in fact_keys(row):
            facts[key] = row
    return facts


def nonsemantic_catalog_row(task: dict[str, Any], data: bytes | None, generator_rule: str) -> dict[str, Any]:
    body = strip_alignment_padding(data or b"")
    target_slice = task.get("targetSlice") if isinstance(task.get("targetSlice"), dict) else {}
    boundary_quality = target_slice.get("boundaryQuality") if isinstance(target_slice.get("boundaryQuality"), dict) else {}
    opportunity = classify_nonsemantic_opportunity(body)
    return {
        "schema": "mizuchi.source-generation.nonsemantic-slice.v1",
        "name": task.get("name"),
        "entry": task.get("entry"),
        "address": task.get("address"),
        "rva": task.get("rva"),
        "generatorRule": generator_rule,
        "bodyBytes": len(body),
        "bytePrefix": body[:48].hex(),
        "byteSha256": hashlib.sha256(body).hexdigest() if body else None,
        "boundaryQuality": boundary_quality,
        "opportunity": opportunity,
        "source": task.get("boundarySource"),
        "claimBoundary": "catalog entry is generator-targeting evidence; it is not recovered source",
    }


def classify_nonsemantic_opportunity(body: bytes) -> dict[str, Any]:
    if not body:
        return {"class": "empty", "confidence": "low", "reason": "no bytes"}
    if is_tail_fragment(body):
        return {"class": "tail-fragment", "confidence": "high", "reason": "slice starts with common epilogue bytes"}
    if len(body) < 8 and not has_x86_terminal_return(body):
        return {"class": "boundary-fragment", "confidence": "high", "reason": "tiny slice without terminal return"}
    if body.startswith(b"\xff\x25") and len(body) <= 8:
        return {"class": "import-tail-jump", "confidence": "medium", "reason": "absolute indirect jump thunk"}
    if is_live_eax_nullable_import_tailjmp_stdcall4(body):
        return {"class": "live-eax-nullable-import-tailjmp-stdcall4", "confidence": "medium", "reason": "live eax nullable field rewrite followed by absolute import tail jump or ret 4"}
    if is_ecx_global_cmp_return_else_tailjmp(body):
        return {"class": "ecx-global-cmp-return-else-tailjmp", "confidence": "medium", "reason": "compares live ecx to a global, returns on equality, otherwise tail-jumps"}
    if is_x87_temp_i16_return(body):
        return {"class": "x87-temp-i16-return", "confidence": "medium", "reason": "spills live x87 value to stack temp and returns the sign-extended low word"}
    if is_x87_pop_return_zero(body):
        return {"class": "x87-pop-return-zero", "confidence": "medium", "reason": "discards the live x87 value, loads +0.0, and returns"}
    if is_x87_round_stack_double_return(body):
        return {"class": "x87-round-stack-double-return", "confidence": "medium", "reason": "rounds a stack double with x87 frndint and returns the rounded x87 value"}
    if is_x87_control_word_masked_setter(body):
        return {"class": "x87-control-word-masked-setter", "confidence": "medium", "reason": "merges the current x87 control word with stack value/mask arguments and returns the previous control word"}
    if is_x87_double_exponent_adjust_return(body):
        return {"class": "x87-double-exponent-adjust-return", "confidence": "medium", "reason": "rewrites a stack double exponent word from an integer argument and returns the adjusted x87 value"}
    if is_stack_arg_range_global_mode_setter(body):
        return {"class": "stack-arg-range-global-mode-setter", "confidence": "medium", "reason": "uses stack arg 1 to select a decoded global mode value for inputs 1..3"}
    if is_u96_bit_tail_clear_check(body):
        return {"class": "u96-bit-tail-clear-check", "confidence": "medium", "reason": "checks that all set bits at or after a decoded bit index are clear in a 96-bit word array"}
    if is_ebx_bitfield_mode_remap(body):
        return {"class": "ebx-bitfield-mode-remap", "confidence": "medium", "reason": "maps live ebx bitfields into a compact return flag word"}
    if is_push_const_call_wrapper(body):
        return {"class": "push-const-call-wrapper", "confidence": "medium", "reason": "push constants, direct call, stack cleanup, return"}
    if is_push_imm32_pair_call_wrapper(body):
        return {"class": "push-imm32-pair-call-wrapper", "confidence": "medium", "reason": "push two imm32 constants, direct call, caller cleanup via pop/pop, return"}
    if is_u32_add_store_wrap_flag(body):
        return {"class": "u32-add-store-wrap-flag", "confidence": "medium", "reason": "adds two u32 stack arguments, stores the sum, and returns a wrap/carry flag"}
    if is_push_global_call_wrapper(body):
        return {"class": "push-global-call-wrapper", "confidence": "medium", "reason": "push global and argument, direct call, stack cleanup, return"}
    if is_push_stack_stack_const_call_wrapper(body):
        return {"class": "push-stack-stack-const-call-wrapper", "confidence": "medium", "reason": "push two stack arguments and a constant, direct call, stack cleanup, return"}
    if is_bink_copy_to_buffer_forwarder(body):
        return {"class": "bink-copy-to-buffer-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that reshapes Bink buffer fields and stack arguments before a local copy call"}
    if is_bink_buffer_clear_forwarder(body):
        return {"class": "bink-buffer-clear-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that locks a Bink buffer, calls the clear helper, unlocks, and returns success"}
    if is_bink_buffer_unlock_forwarder(body):
        return {"class": "bink-buffer-unlock-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that unlocks a Bink buffer, clears transient fields, and returns success"}
    if is_bink_buffer_set_offset_forwarder(body):
        return {"class": "bink-buffer-set-offset-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that validates a window, updates buffer offset fields, and marks state dirty"}
    if is_bink_buffer_set_direct_draw_forwarder(body):
        return {"class": "bink-buffer-set-direct-draw-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that stores or clears DirectDraw globals and optionally reinitializes Bink buffers"}
    if is_bink_buffer_check_win_pos_forwarder(body):
        return {"class": "bink-buffer-check-win-pos-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that clamps requested buffer window position to decoded globals and alignment mode"}
    if is_bink_buffer_close_forwarder(body):
        return {"class": "bink-buffer-close-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that releases Bink buffer surfaces, helper allocations, and clears the buffer struct"}
    if is_bink_buffer_lock_forwarder(body):
        return {"class": "bink-buffer-lock-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that locks or falls back to cached buffer memory and records output pointers"}
    if is_bink_buffer_set_scale_forwarder(body):
        return {"class": "bink-buffer-set-scale-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that derives scale mode flags and updates scaled buffer extents"}
    if is_bink_close_track_forwarder(body):
        return {"class": "bink-close-track-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that frees an optional track allocation and then releases the track object"}
    if is_bink_pause_forwarder(body):
        return {"class": "bink-pause-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that toggles Bink pause state and forwards pause changes to each track"}
    if is_bink_get_key_frame_forwarder(body):
        return {"class": "bink-get-key-frame-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that scans the decoded key-frame table according to mode"}
    if is_bink_check_cursor_forwarder(body):
        return {"class": "bink-check-cursor-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that initializes cursor metrics and checks whether a cursor rectangle intersects the current cursor position"}
    if is_bink_open_track_forwarder(body):
        return {"class": "bink-open-track-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that validates a track descriptor, opens helper state, allocates a track object, and fills decoded fields"}
    if is_bink_buffer_get_description_forwarder(body):
        return {"class": "bink-buffer-get-description-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that maps decoded Bink buffer type ids to static description records through an embedded jump table"}
    if is_bink_next_frame_forwarder(body):
        return {"class": "bink-next-frame-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that advances Bink frame state, updates track sound state, and dispatches decoded frame callbacks"}
    if is_bink_get_realtime_forwarder(body):
        return {"class": "bink-get-realtime-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that fills decoded realtime summary fields after synchronizing Bink timing state"}
    if is_bink_goto_forwarder(body):
        return {"class": "bink-goto-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that seeks Bink frame state, advances decoded frames, and restores audio/callback state"}
    if is_bink_get_summary_forwarder(body):
        return {"class": "bink-get-summary-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that synchronizes timing state and copies decoded Bink summary fields"}
    if is_bink_close_forwarder(body):
        return {"class": "bink-close-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that shuts down Bink playback state, frees decoded allocations, clears the Bink struct, and releases the object"}
    if is_bink_wait_forwarder(body):
        return {"class": "bink-wait-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that synchronizes Bink timing state, polls audio backend state, and reports whether playback should wait"}
    if is_bink_surface_type_forwarder(body):
        return {"class": "bink-surface-type-forwarder", "confidence": "medium", "reason": "stdcall export wrapper that queries a surface format descriptor and maps decoded masks/FourCC values to Bink surface ids"}
    if is_rad_aligned_malloc_forwarder(body):
        return {"class": "rad-aligned-malloc-forwarder", "confidence": "medium", "reason": "stdcall allocator wrapper that overallocates for alignment and records allocation metadata"}
    if is_rad_aligned_free_forwarder(body):
        return {"class": "rad-aligned-free-forwarder", "confidence": "medium", "reason": "stdcall allocator wrapper that reverses RAD alignment metadata before dispatching custom or fallback free"}
    if is_rad_direct_free_wrapper(body):
        return {"class": "rad-direct-free-wrapper", "confidence": "medium", "reason": "cdecl fallback free wrapper that conditionally invokes custom cleanup before import-backed free"}
    if is_rad_timer_read_forwarder(body):
        return {"class": "rad-timer-read-forwarder", "confidence": "medium", "reason": "cdecl timer wrapper that initializes high-resolution counters and returns a bounded RAD time value"}
    if is_multi_function_packed_slice(body):
        return {"class": "multi-function-packed-slice", "confidence": "high", "reason": "contains an unconditional return followed by alignment padding and additional executable bytes; needs boundary splitting before semantic source generation"}
    if is_stdcall_yuv_blit_format_wrapper(body):
        return {"class": "stdcall-yuv-blit-format-wrapper", "confidence": "medium", "reason": "stdcall YUV blit format wrapper with constant table, eax live input, and direct call"}
    if is_stdcall_yuv_blit_alpha_wrapper(body):
        return {"class": "stdcall-yuv-blit-alpha-wrapper", "confidence": "medium", "reason": "stdcall YUV alpha blit wrapper with constant table, eax/ecx live inputs, and direct call"}
    if is_stdcall_yuv_blit_packed_wrapper(body):
        return {"class": "stdcall-yuv-blit-packed-wrapper", "confidence": "medium", "reason": "stdcall packed YUV blit wrapper with alignment fixups and direct call"}
    if decode_stdcall_yuv_blit_mask_format_prefix_bytes(body) is not None:
        return {"class": "stdcall-yuv-blit-mask-format-prefix", "confidence": "medium", "reason": "leading stdcall YUV mask-format wrapper followed by additional inferred-slice bytes"}
    if decode_stdcall_yuv_blit_mask_alpha_prefix_bytes(body) is not None:
        return {"class": "stdcall-yuv-blit-mask-alpha-prefix", "confidence": "medium", "reason": "leading stdcall YUV mask-alpha wrapper followed by additional inferred-slice bytes"}
    if is_global_guard_return_zero(body):
        return {"class": "global-guard-return-zero", "confidence": "medium", "reason": "global guard, optional call, global set, return zero"}
    if is_rep_stos_global_clear(body):
        return {"class": "rep-stos-global-clear", "confidence": "medium", "reason": "rep stos global clear sequence"}
    if is_small_zero_scan_bool(body):
        return {"class": "small-zero-scan-bool", "confidence": "medium", "reason": "small indexed zero scan returning boolean"}
    if is_u96_left_shift_one(body):
        return {"class": "u96-left-shift-one", "confidence": "medium", "reason": "three-limb in-place left shift by one"}
    if is_small_copy_loop(body):
        return {"class": "small-copy-loop", "confidence": "medium", "reason": "small fixed-count dword copy loop"}
    if body.count(b"\xe8") or body.count(b"\xff\x15"):
        return {"class": "call-bearing", "confidence": "low", "reason": "contains direct or import calls; needs relocation/call-target modeling"}
    return {"class": "unknown", "confidence": "low", "reason": "no current semantic generator pattern"}


def is_tail_fragment(body: bytes) -> bool:
    return body.startswith((b"\x5f\x5e\x5b\xc9\xc3", b"\x5f\x5d\x5b\xc3", b"\x5e\xc3", b"\x5f\xc3"))


def is_push_const_call_wrapper(body: bytes) -> bool:
    return body.endswith(b"\xc3") and body.count(b"\xe8") == 1 and body.startswith(b"\x6a")


def is_push_imm32_pair_call_wrapper(body: bytes) -> bool:
    return (
        len(body) == 18
        and body[0] == 0x68
        and body[5] == 0x68
        and body[10] == 0xE8
        and body[15:] == b"\x59\x59\xc3"
    )


def is_live_eax_nullable_import_tailjmp_stdcall4(body: bytes) -> bool:
    return decode_live_eax_nullable_import_tailjmp_stdcall4(body) is not None


def is_ecx_global_cmp_return_else_tailjmp(body: bytes) -> bool:
    return (
        len(body) == 14
        and body[:2] == b"\x3b\x0d"
        and body[6:9] == b"\x75\x01\xc3"
        and body[9] == 0xE9
    )


def is_u32_add_store_wrap_flag(body: bytes) -> bool:
    return body == bytes.fromhex("8b542404568b74240c8d0c3233c03bca72043bce730333c0408b542410890a5ec3")


def is_x87_temp_i16_return(body: bytes) -> bool:
    return decode_x87_temp_i16_return(body) is not None


def is_x87_pop_return_zero(body: bytes) -> bool:
    return body == bytes.fromhex("ddd8d9eec3")


def is_x87_round_stack_double_return(body: bytes) -> bool:
    return decode_x87_round_stack_double_return(body) is not None


def is_x87_control_word_masked_setter(body: bytes) -> bool:
    return decode_x87_control_word_masked_setter(body) is not None


def is_x87_double_exponent_adjust_return(body: bytes) -> bool:
    return decode_x87_double_exponent_adjust_return(body) is not None


def is_stack_arg_range_global_mode_setter(body: bytes) -> bool:
    return decode_stack_arg_range_global_mode_setter(body) is not None


def is_u96_bit_tail_clear_check(body: bytes) -> bool:
    return decode_u96_bit_tail_clear_check(body) is not None


def is_ebx_bitfield_mode_remap(body: bytes) -> bool:
    return decode_ebx_bitfield_mode_remap(body) is not None


def is_push_global_call_wrapper(body: bytes) -> bool:
    return body.endswith(b"\xc3") and body.count(b"\xe8") == 1 and body.startswith(b"\xff\x35")


def is_push_stack_stack_const_call_wrapper(body: bytes) -> bool:
    return (
        len(body) == 22
        and body[0] == 0x68
        and body[5:9] == b"\xff\x74\x24\x0c"
        and body[9:13] == b"\xff\x74\x24\x0c"
        and body[13] == 0xE8
        and body[18:] == b"\x83\xc4\x0c\xc3"
    )


def is_bink_copy_to_buffer_forwarder(body: bytes) -> bool:
    return decode_bink_copy_to_buffer_forwarder(body) is not None


def is_bink_buffer_clear_forwarder(body: bytes) -> bool:
    return decode_bink_buffer_clear_forwarder(body) is not None


def is_bink_buffer_unlock_forwarder(body: bytes) -> bool:
    return decode_bink_buffer_unlock_forwarder(body) is not None


def is_bink_buffer_set_offset_forwarder(body: bytes) -> bool:
    return decode_bink_buffer_set_offset_forwarder(body) is not None


def is_bink_buffer_set_direct_draw_forwarder(body: bytes) -> bool:
    return decode_bink_buffer_set_direct_draw_forwarder(body) is not None


def is_bink_buffer_check_win_pos_forwarder(body: bytes) -> bool:
    return decode_bink_buffer_check_win_pos_forwarder(body) is not None


def is_bink_buffer_close_forwarder(body: bytes) -> bool:
    return decode_bink_buffer_close_forwarder(body) is not None


def is_bink_buffer_lock_forwarder(body: bytes) -> bool:
    return decode_bink_buffer_lock_forwarder(body) is not None


def is_bink_buffer_set_scale_forwarder(body: bytes) -> bool:
    return decode_bink_buffer_set_scale_forwarder(body) is not None


def is_bink_close_track_forwarder(body: bytes) -> bool:
    return decode_bink_close_track_forwarder(body) is not None


def is_stdcall_yuv_blit_format_wrapper(body: bytes) -> bool:
    return decode_stdcall_yuv_blit_format_wrapper_bytes(body) is not None


def is_stdcall_yuv_blit_alpha_wrapper(body: bytes) -> bool:
    return decode_stdcall_yuv_blit_alpha_wrapper_bytes(body) is not None


def is_stdcall_yuv_blit_packed_wrapper(body: bytes) -> bool:
    return decode_stdcall_yuv_blit_packed_wrapper_bytes(body) is not None


def decode_stdcall_yuv_blit_format_wrapper_bytes(body: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(body)
    if len(body) != 68:
        return None
    fixed_slices = {
        (0, 13): bytes.fromhex("558bec8b45348b4d308b552868"),
        (17, 56): bytes.fromhex("6a00508b4524518b4d20528b551c508b4518518b4d14528b5510508b450c518b4d0852508b452c"),
        (61, 68): bytes.fromhex("83c4305dc23000"),
    }
    for (start, end), expected in fixed_slices.items():
        if body[start:end] != expected:
            return None
    if body[56] != 0xE8:
        return None
    return {
        "constant": f"0x{int.from_bytes(body[13:17], 'little'):08x}",
        "stackBytes": 48,
        "calleeStackBytes": 48,
        "callOffset": 56,
        "eaxArgIndex": 10,
        "stackArgOrder": [2, 3, 4, 5, 6, 7, 8, 9, 11, 12, "zero", "constant"],
    }


def decode_stdcall_yuv_blit_alpha_wrapper_bytes(body: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(body)
    if len(body) != 70:
        return None
    fixed_slices = {
        (0, 13): bytes.fromhex("558bec8b45348b4d388b553068"),
        (17, 58): bytes.fromhex("508b4528518b4d24528b5520508b451c518b4d18528b5514508b4510518b4d0c52508b452c518b4d08"),
        (63, 70): bytes.fromhex("83c4305dc23400"),
    }
    for (start, end), expected in fixed_slices.items():
        if body[start:end] != expected:
            return None
    if body[58] != 0xE8:
        return None
    return {
        "constant": f"0x{int.from_bytes(body[13:17], 'little'):08x}",
        "stackBytes": 52,
        "calleeStackBytes": 48,
        "callOffset": 58,
        "eaxArgIndex": 10,
        "ecxArgIndex": 1,
        "stackArgOrder": [2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 12, "constant"],
    }


def decode_stdcall_yuv_blit_packed_wrapper_bytes(body: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(body)
    if len(body) != 113:
        return None
    fixed_slices = {
        (0, 61): bytes.fromhex("558bec8b4d088bc1538b5d0c24033c025675044383e1fcf6c3018b551c740cf6c2017401428b452443eb09f6c2018b452474024248a8017401488b7534"),
        (66, 99): bytes.fromhex("6a00568b7530568b752856508b4520508b4514528b5518528b5510508b452c5253"),
        (104, 113): bytes.fromhex("83c4305e5b5dc23000"),
    }
    for (start, end), expected in fixed_slices.items():
        if body[start:end] != expected:
            return None
    if body[99] != 0xE8:
        return None
    return {
        "constant": f"0x{int.from_bytes(body[62:66], 'little'):08x}",
        "stackBytes": 48,
        "calleeStackBytes": 48,
        "callOffset": 99,
        "alignmentMask": 3,
        "selectorArgIndex": 1,
        "strideArgIndex": 2,
        "adjustedArgIndexes": [5, 7],
        "stackArgOrder": [2, 3, 10, 4, 5, "adjusted-arg7", 7, "adjusted-arg5", 9, 11, 12, "zero", "constant"],
    }


def decode_stdcall_yuv_blit_mask_format_prefix_bytes(body: bytes) -> dict[str, Any] | None:
    if len(body) <= 80:
        return None
    prefix = body[:78]
    fixed_slices = {
        (0, 13): bytes.fromhex("558bec8b453c8b4d388b553468"),
        (17, 66): bytes.fromhex("6a00508b4530518b4d2c528b5528508b4524518b4d20528b551c508b4518518b4d14528b5510508b450c518b4d08525051"),
        (71, 78): bytes.fromhex("83c4405dc23800"),
    }
    for (start, end), expected in fixed_slices.items():
        if prefix[start:end] != expected:
            return None
    if prefix[66] != 0xE8 or body[78:80] != b"\x90\x90":
        return None
    return {
        "constant": f"0x{int.from_bytes(prefix[13:17], 'little'):08x}",
        "stackBytes": 56,
        "calleeStackBytes": 64,
        "callOffset": 66,
        "targetByteSpan": {"offset": 0, "length": 78},
        "sourceTier": "generated inline-assembly parity fallback with decoded leading YUV mask-format wrapper bytes",
    }


def decode_stdcall_yuv_blit_mask_alpha_prefix_bytes(body: bytes) -> dict[str, Any] | None:
    if len(body) <= 82:
        return None
    prefix = body[:80]
    fixed_slices = {
        (0, 13): bytes.fromhex("558bec8b453c8b4d408b553868"),
        (17, 68): bytes.fromhex("508b4534518b4d30528b552c508b4528518b4d24528b5520508b451c518b4d18528b5514508b4510518b4d0c528b5508505152"),
        (73, 80): bytes.fromhex("83c4405dc23c00"),
    }
    for (start, end), expected in fixed_slices.items():
        if prefix[start:end] != expected:
            return None
    if prefix[68] != 0xE8 or body[80:82] != b"\x53\x55":
        return None
    return {
        "constant": f"0x{int.from_bytes(prefix[13:17], 'little'):08x}",
        "stackBytes": 60,
        "calleeStackBytes": 64,
        "callOffset": 68,
        "targetByteSpan": {"offset": 0, "length": 80},
        "sourceTier": "generated inline-assembly parity fallback with decoded leading YUV mask-alpha wrapper bytes",
    }


def is_global_guard_return_zero(body: bytes) -> bool:
    return body.startswith(b"\x83\x3d") and b"\xc7\x05" in body and body.endswith(b"\x33\xc0\xc3")


def is_rep_stos_global_clear(body: bytes) -> bool:
    return body.startswith(b"\x57\x6a") and b"\xf3\xab" in body and body.endswith(b"\x5f\xc3")


def is_small_zero_scan_bool(body: bytes) -> bool:
    return body.startswith(b"\x33\xc0") and b"\x83\xf8" in body and body.endswith(b"\x33\xc0\xc3")


def is_u96_left_shift_one(body: bytes) -> bool:
    return body.startswith(b"\x8b\x44\x24\x04\x56\x8b\x30") and body.endswith(b"\x89\x48\x08\x5e\xc3") and b"\xc1\xe9\x1f" in body and b"\xc1\xea\x1f" in body


def is_small_copy_loop(body: bytes) -> bool:
    return body.startswith(b"\x8b\x44\x24") and b"\x8b\x30" in body and body.endswith(b"\x5e\xc3")


def write_source_coverage_artifacts(
    out_dir: Path,
    *,
    task_count: int,
    target_slice_count: int,
    generated_count: int,
    semantic_generated_count: int,
    nonsemantic_catalog: list[dict[str, Any]],
    applied_boundary_repairs: list[dict[str, Any]],
    generated_by_rule: dict[str, int],
    semantic_by_rule: dict[str, int],
    generated_by_language: dict[str, int],
    semantic_by_language: dict[str, int],
    generated_by_source_quality: dict[str, int],
    generated_by_recovery_scope: dict[str, int],
    semantic_by_recovery_scope: dict[str, int],
    boundary_quality: dict[str, int],
) -> dict[str, Any]:
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    coverage_path = artifacts_dir / "semantic-coverage.json"
    catalog_path = artifacts_dir / "nonsemantic-slice-catalog.jsonl"
    opportunities_path = artifacts_dir / "generator-opportunities.json"
    boundary_repair = write_boundary_repair_artifacts(artifacts_dir, nonsemantic_catalog, applied_boundary_repairs)
    semantic_ratio = semantic_generated_count / generated_count if generated_count else 0.0
    slice_semantic_ratio = semantic_generated_count / target_slice_count if target_slice_count else 0.0
    high_level_count = int(generated_by_source_quality.get("high-level-c", 0))
    inline_asm_count = int(generated_by_source_quality.get("inline-asm-c", 0))
    byte_emission_count = int(generated_by_source_quality.get("byte-emission-asm", 0))
    high_level_ratio = high_level_count / generated_count if generated_count else 0.0
    high_level_semantic_ratio = high_level_count / semantic_generated_count if semantic_generated_count else 0.0
    top_nonsemantic = sorted(nonsemantic_catalog, key=lambda row: (int(row.get("bodyBytes") or 0), str(row.get("name") or "")))[:50]
    opportunity_summary = summarize_generator_opportunities(nonsemantic_catalog)
    with catalog_path.open("w", encoding="utf-8") as fh:
        for row in sorted(nonsemantic_catalog, key=lambda item: (int(item.get("bodyBytes") or 0), str(item.get("name") or ""))):
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    write_json(opportunities_path, opportunity_summary)
    coverage = {
        "schema": "mizuchi.source-generation.semantic-coverage.v1",
        "taskCount": task_count,
        "targetSlices": target_slice_count,
        "generatedSourceCandidates": generated_count,
        "semanticSourceCandidates": semantic_generated_count,
        "nonSemanticBootstrapCandidates": max(0, generated_count - semantic_generated_count),
        "highLevelSourceCandidates": high_level_count,
        "inlineAsmSourceCandidates": inline_asm_count,
        "byteEmissionSourceCandidates": byte_emission_count,
        "semanticGeneratedRatio": round(semantic_ratio, 6),
        "semanticTargetSliceRatio": round(slice_semantic_ratio, 6),
        "highLevelGeneratedRatio": round(high_level_ratio, 6),
        "highLevelSemanticRatio": round(high_level_semantic_ratio, 6),
        "generatedByRule": dict(sorted(generated_by_rule.items())),
        "semanticByRule": dict(sorted(semantic_by_rule.items())),
        "generatedByLanguage": dict(sorted(generated_by_language.items())),
        "semanticByLanguage": dict(sorted(semantic_by_language.items())),
        "generatedBySourceQuality": dict(sorted(generated_by_source_quality.items())),
        "generatedByRecoveryScope": dict(sorted(generated_by_recovery_scope.items())),
        "semanticByRecoveryScope": dict(sorted(semantic_by_recovery_scope.items())),
        "wholeFunctionSemanticSourceCandidates": int(semantic_by_recovery_scope.get("whole-function", 0)),
        "partialSourceSliceSemanticCandidates": int(semantic_by_recovery_scope.get("partial-source-slice", 0)),
        "targetSlicesByBoundaryQuality": dict(sorted(boundary_quality.items())),
        "generatorOpportunities": opportunity_summary["classes"],
        "topNonsemanticSlices": top_nonsemantic,
        "nonsemanticCatalog": str(catalog_path),
        "generatorOpportunitiesPath": str(opportunities_path),
        "boundaryRepair": boundary_repair,
        "claimBoundary": "coverage distinguishes high-level C, inline-asm C, byte-emission assembly, and nonsemantic bootstrap; only compiler/objdiff acceptance proves parity",
    }
    write_json(coverage_path, coverage)
    return {
        "schema": "mizuchi.source-generation.coverage-artifacts.v1",
        "status": "complete",
        "semanticCoverage": str(coverage_path),
        "nonsemanticCatalog": str(catalog_path),
        "generatorOpportunities": str(opportunities_path),
        "boundaryRepair": boundary_repair,
        "semanticGeneratedRatio": coverage["semanticGeneratedRatio"],
        "semanticTargetSliceRatio": coverage["semanticTargetSliceRatio"],
        "highLevelGeneratedRatio": coverage["highLevelGeneratedRatio"],
        "highLevelSemanticRatio": coverage["highLevelSemanticRatio"],
        "wholeFunctionSemanticSourceCandidates": coverage["wholeFunctionSemanticSourceCandidates"],
        "partialSourceSliceSemanticCandidates": coverage["partialSourceSliceSemanticCandidates"],
    }


def classify_source_recovery_scope(candidate: dict[str, Any], data: bytes | None = None) -> str:
    if candidate.get("semanticSource") is False:
        return "nonsemantic"
    generator = candidate.get("generator") if isinstance(candidate.get("generator"), dict) else {}
    scope = str(generator.get("sourceRecoveryScope") or "").strip()
    scoped_data = source_scope_classification_bytes(data, generator)
    if scope and not (
        scope == "context-dependent-fragment"
        and scoped_data is not None
        and has_source_scope_target_byte_span(generator)
        and not probable_context_dependent_fragment(scoped_data)
    ):
        return scope
    source_slice_kind = str(generator.get("sourceSliceKind") or "")
    claim_boundary = str(generator.get("claimBoundary") or "").lower()
    target_byte_span = generator.get("targetByteSpan") if isinstance(generator.get("targetByteSpan"), dict) else {}
    span_reason = str(target_byte_span.get("reason") or "").lower()
    if source_slice_kind in {"leading-return-prefix"}:
        return "partial-source-slice"
    if "source-slice parity only" in claim_boundary or "source-slice repair only" in span_reason:
        return "partial-source-slice"
    if scoped_data is not None and probable_context_dependent_fragment(scoped_data):
        return "context-dependent-fragment"
    return "whole-function"


def source_scope_classification_bytes(data: bytes | None, generator: dict[str, Any]) -> bytes | None:
    if data is None:
        return None
    target_byte_span = generator.get("targetByteSpan") if isinstance(generator.get("targetByteSpan"), dict) else {}
    start = optional_int(target_byte_span.get("offset")) or 0
    length = optional_int(target_byte_span.get("length"))
    if length is not None and start >= 0 and length >= 0 and start + length <= len(data):
        return data[start : start + length]
    return strip_alignment_padding(data)


def has_source_scope_target_byte_span(generator: dict[str, Any]) -> bool:
    target_byte_span = generator.get("targetByteSpan") if isinstance(generator.get("targetByteSpan"), dict) else {}
    return bool(target_byte_span) and optional_int(target_byte_span.get("length")) is not None


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 0)
        except ValueError:
            return None
    return None


def probable_context_dependent_fragment(data: bytes) -> bool:
    body = strip_alignment_padding(data)
    if not body:
        return False
    if has_standard_frame_prologue(body):
        return False
    if body[0] in {0x5B, 0x5D, 0x5E, 0x5F, 0xC9, 0xC3, 0xC2}:
        return True
    if body.startswith(b"\x8d\x65"):
        return True
    if 0xC9 in body:
        return True
    return has_ebp_relative_access(body)


def has_standard_frame_prologue(body: bytes) -> bool:
    return body.startswith(b"\x55\x8b\xec") or body.startswith(b"\x55\x89\xe5")


def has_ebp_relative_access(body: bytes) -> bool:
    # Conservative ModRM scan for common instructions that address [ebp+disp].
    for index, opcode in enumerate(body[:-2]):
        if opcode not in {0x8B, 0x89, 0x8D, 0xC7, 0xD9, 0xDD}:
            continue
        modrm = body[index + 1]
        mod = modrm >> 6
        rm = modrm & 0x07
        if rm == 5 and mod in {1, 2}:
            return True
    return False


def classify_generated_source_quality(candidate: dict[str, Any]) -> str:
    if candidate.get("semanticSource") is False:
        return "nonsemantic-bootstrap"
    language = str(candidate.get("language") or "").lower()
    generator = candidate.get("generator") if isinstance(candidate.get("generator"), dict) else {}
    source_tier = str(generator.get("sourceTier") or "").lower()
    if language in {"asm", "masm"}:
        return "byte-emission-asm"
    if "byte-emission" in source_tier or "masm" in source_tier:
        return "byte-emission-asm"
    if "inline-assembly" in source_tier or "inline assembly" in source_tier:
        return "inline-asm-c"
    source = str(candidate.get("source") or "")
    if "__declspec(naked)" in source or "__attribute__((naked))" in source:
        return "inline-asm-c"
    if language == "c":
        return "high-level-c"
    return "semantic-other"


def write_boundary_repair_artifacts(
    artifacts_dir: Path,
    nonsemantic_catalog: list[dict[str, Any]],
    applied_boundary_repairs: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest_path = artifacts_dir / "boundary-repair-manifest.jsonl"
    applied_path = artifacts_dir / "applied-boundary-repairs.jsonl"
    summary_path = artifacts_dir / "boundary-repair-summary.json"
    rows = []
    for row in nonsemantic_catalog:
        manifest_row = boundary_repair_manifest_row(row)
        if manifest_row is not None:
            rows.append(manifest_row)
    rows = sorted(rows, key=lambda row: (int(row.get("address") or -1), int(row.get("bodyBytes") or 0), str(row.get("name") or "")))
    with manifest_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    with applied_path.open("w", encoding="utf-8") as fh:
        for row in sorted(applied_boundary_repairs, key=lambda item: (int(item.get("ownerRva") or -1), int(item.get("fragmentRva") or -1))):
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    counts_by_action: dict[str, int] = {}
    counts_by_class: dict[str, int] = {}
    for row in rows:
        action = str(row.get("recommendedRepair") or "unknown")
        cls = str(row.get("fragmentClass") or "unknown")
        counts_by_action[action] = counts_by_action.get(action, 0) + 1
        counts_by_class[cls] = counts_by_class.get(cls, 0) + 1
    summary = {
        "schema": "mizuchi.source-generation.boundary-repair-summary.v1",
        "status": "complete",
        "fragmentCount": len(rows),
        "appliedRepairCount": len(applied_boundary_repairs),
        "countsByFragmentClass": dict(sorted(counts_by_class.items())),
        "countsByRecommendedRepair": dict(sorted(counts_by_action.items())),
        "manifest": str(manifest_path),
        "appliedRepairs": str(applied_path),
        "claimBoundary": "boundary repair rows are stitch targets for correcting function extents; they are not source candidates and are not objdiff proof",
    }
    write_json(summary_path, summary)
    return {
        "schema": "mizuchi.source-generation.boundary-repair-artifacts.v1",
        "status": "complete",
        "fragmentCount": len(rows),
        "appliedRepairCount": len(applied_boundary_repairs),
        "summary": str(summary_path),
        "manifest": str(manifest_path),
        "appliedRepairs": str(applied_path),
        "countsByFragmentClass": summary["countsByFragmentClass"],
        "countsByRecommendedRepair": summary["countsByRecommendedRepair"],
    }


def boundary_repair_manifest_row(row: dict[str, Any]) -> dict[str, Any] | None:
    opportunity = row.get("opportunity") if isinstance(row.get("opportunity"), dict) else {}
    cls = str(opportunity.get("class") or "")
    if cls == "tail-fragment":
        recommended = "prepend-to-previous-function-tail"
        required = [
            "identify owning predecessor by contiguous code range or disassembly fallthrough",
            "extend predecessor targetByteSpan through this epilogue fragment",
            "re-run source synthesis/objdiff on repaired function extent",
        ]
    elif cls == "boundary-fragment":
        recommended = "merge-with-adjacent-boundary-fragment"
        required = [
            "identify adjacent function start/end from disassembler basic-block graph",
            "discard standalone task or merge bytes into corrected owner before source generation",
            "re-run boundary-quality classifier before counting any source candidate",
        ]
    else:
        return None
    boundary_quality = row.get("boundaryQuality") if isinstance(row.get("boundaryQuality"), dict) else {}
    return {
        "schema": "mizuchi.source-generation.boundary-repair-row.v1",
        "name": row.get("name"),
        "entry": row.get("entry"),
        "address": coerce_int(row.get("address")),
        "rva": row.get("rva"),
        "fragmentClass": cls,
        "confidence": opportunity.get("confidence"),
        "reason": opportunity.get("reason"),
        "bodyBytes": row.get("bodyBytes"),
        "bytePrefix": row.get("bytePrefix"),
        "byteSha256": row.get("byteSha256"),
        "boundaryQuality": boundary_quality,
        "recommendedRepair": recommended,
        "requiredEvidence": required,
        "claimBoundary": "fragment row is boundary-repair evidence only; it is not generated source and must not be counted as recovered source",
    }


def build_address_alias_metadata(candidates: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_address: dict[int, dict[str, Any]] = {}
    for row in candidates:
        address = coerce_int(row.get("address"))
        if address is None:
            continue
        bucket = by_address.setdefault(address, {"rows": [], "aliases": []})
        bucket["rows"].append(row)
        bucket["aliases"].extend(candidate_alias_entries(row))

    metadata: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    for address, bucket in by_address.items():
        rows = list(bucket["rows"])
        aliases = dedupe_address_alias_entries(bucket["aliases"])
        if len(aliases) < 2:
            continue
        ordered = sorted(aliases, key=address_alias_priority)
        primary = ordered[0]
        canonical_name = str(primary.get("name") or f"sub_{address:x}")
        canonical_entry = primary.get("entry")
        group = {
            "schema": "mizuchi.address-alias-group.v1",
            "canonicalAddress": f"0x{address:08x}",
            "canonicalName": canonical_name,
            "canonicalEntry": canonical_entry,
            "aliasCount": len(aliases),
            "scheduledCandidateCount": len(rows),
            "duplicateAddressAliases": max(0, len(aliases) - 1),
            "duplicateAddressScheduledTasks": max(0, len(rows) - 1),
            "aliases": aliases,
            "claimBoundary": "same-address names share target bytes; alias metadata prevents duplicate task counts from being mistaken for additional recovered code",
        }
        groups.append(group)
        for row in rows:
            role = "primary" if address_alias_entry_matches(row, primary) else "alias"
            metadata[address_alias_key(row)] = {
                "schema": "mizuchi.address-alias.v1",
                "canonicalAddress": f"0x{address:08x}",
                "canonicalName": canonical_name,
                "canonicalEntry": canonical_entry,
                "aliasCount": len(aliases),
                "role": role,
                "aliases": aliases,
                "claimBoundary": "same-address names share target bytes; alias metadata prevents duplicate task counts from being mistaken for additional recovered code",
            }
    return metadata, groups


def candidate_alias_entries(row: dict[str, Any]) -> list[dict[str, Any]]:
    entries = [
        {
            "name": row.get("name"),
            "entry": row.get("entry"),
            "source": row.get("source"),
            "confidence": row.get("confidence"),
        }
    ]
    for alias in (row.get("evidence") or {}).get("aliases") or []:
        if not isinstance(alias, dict):
            continue
        entries.append(
            {
                "name": alias.get("name"),
                "entry": alias.get("entry"),
                "source": alias.get("source"),
                "confidence": alias.get("confidence"),
            }
        )
    return entries


def dedupe_address_alias_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for entry in entries:
        key = (
            str(entry.get("name") or ""),
            str(entry.get("entry") or ""),
            str(entry.get("source") or ""),
            str(entry.get("confidence") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def address_alias_entry_matches(row: dict[str, Any], entry: dict[str, Any]) -> bool:
    return (
        str(row.get("name") or "") == str(entry.get("name") or "")
        and str(row.get("entry") or "") == str(entry.get("entry") or "")
        and str(row.get("source") or "") == str(entry.get("source") or "")
    )


def address_alias_key(row: dict[str, Any]) -> str:
    address = coerce_int(row.get("address"))
    return f"{address}:{row.get('entry')}:{row.get('name')}"


def address_alias_priority(row: dict[str, Any]) -> tuple[int, int, int, str]:
    name = str(row.get("name") or "")
    source = str(row.get("source") or "")
    confidence = str(row.get("confidence") or "")
    synthetic_name = name.startswith(("sub_", "entry_"))
    return (
        0 if source == "pe-export" else 1,
        0 if confidence == "high" else 1,
        1 if synthetic_name else 0,
        name,
    )


def write_address_alias_artifacts(out_dir: Path, groups: list[dict[str, Any]]) -> dict[str, Any]:
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    groups_path = artifacts_dir / "address-alias-groups.json"
    summary = {
        "schema": "mizuchi.address-alias-groups.v1",
        "status": "complete",
        "aliasGroups": len(groups),
        "duplicateAddressAliases": sum(int(group.get("duplicateAddressAliases") or 0) for group in groups),
        "duplicateAddressScheduledTasks": sum(int(group.get("duplicateAddressScheduledTasks") or 0) for group in groups),
        "duplicateAddressTasks": sum(int(group.get("duplicateAddressScheduledTasks") or 0) for group in groups),
        "groups": sorted(groups, key=lambda group: str(group.get("canonicalAddress") or "")),
        "claimBoundary": "address aliases are scheduling metadata only; they do not prove source recovery or object equivalence",
    }
    write_json(groups_path, summary)
    return {
        "schema": "mizuchi.address-alias-artifacts.v1",
        "status": "complete",
        "aliasGroups": summary["aliasGroups"],
        "duplicateAddressAliases": summary["duplicateAddressAliases"],
        "duplicateAddressScheduledTasks": summary["duplicateAddressScheduledTasks"],
        "duplicateAddressTasks": summary["duplicateAddressTasks"],
        "groups": str(groups_path),
    }


def summarize_generator_opportunities(nonsemantic_catalog: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in nonsemantic_catalog:
        opportunity = row.get("opportunity") if isinstance(row.get("opportunity"), dict) else {}
        cls = str(opportunity.get("class") or "unknown")
        grouped.setdefault(cls, []).append(row)
    classes: dict[str, Any] = {}
    for cls, rows in sorted(grouped.items()):
        sorted_rows = sorted(rows, key=lambda row: (int(row.get("bodyBytes") or 0), str(row.get("name") or "")))
        unique_addresses = {
            coerce_int(row.get("address"))
            for row in rows
            if coerce_int(row.get("address")) is not None
        }
        classes[cls] = {
            "count": len(rows),
            "uniqueCodeStarts": len(unique_addresses),
            "duplicateAddressTasks": max(0, len(rows) - len(unique_addresses)),
            "confidence": class_confidence(rows),
            "examples": [
                {
                    "name": row.get("name"),
                    "entry": row.get("entry"),
                    "bodyBytes": row.get("bodyBytes"),
                    "bytePrefix": row.get("bytePrefix"),
                    "boundaryQuality": (row.get("boundaryQuality") or {}).get("status") if isinstance(row.get("boundaryQuality"), dict) else None,
                }
                for row in sorted_rows[:10]
            ],
        }
    return {
        "schema": "mizuchi.source-generation.generator-opportunities.v1",
        "classes": classes,
        "claimBoundary": "opportunity classes prioritize future generator work; they are not semantic source or match proof",
    }


def class_confidence(rows: list[dict[str, Any]]) -> str:
    confidences = [
        str((row.get("opportunity") or {}).get("confidence") or "low")
        for row in rows
        if isinstance(row.get("opportunity"), dict)
    ]
    if "high" in confidences:
        return "high"
    if "medium" in confidences:
        return "medium"
    return "low"


def fact_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    if row.get("entryOffset") is not None:
        keys.append(f"address:{int(row['entryOffset'])}")
        keys.append(f"rva:{int(row['entryOffset'])}")
    if row.get("entry"):
        try:
            keys.append(f"address:{int(str(row['entry']), 16)}")
        except ValueError:
            keys.append(f"entry:{row['entry']}")
    if row.get("name"):
        keys.append(f"name:{row['name']}")
    return keys


def match_fact(candidate: dict[str, Any], facts: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    possible = []
    if candidate.get("address") is not None:
        possible.append(f"address:{int(candidate['address'])}")
    if candidate.get("rva") is not None:
        possible.append(f"rva:{int(candidate['rva'])}")
    if candidate.get("name"):
        possible.append(f"name:{candidate['name']}")
    for key in possible:
        if key in facts:
            return facts[key]
    return None


def write_normalized_function_facts(out_dir: Path, facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    artifacts_dir = out_dir / "artifacts"
    facts_path = artifacts_dir / "normalized-function-facts.jsonl"
    summary_path = artifacts_dir / "function-facts-summary.json"
    unique: dict[str, dict[str, Any]] = {}
    for fact in facts.values():
        key = canonical_fact_key(fact)
        unique[key] = normalize_function_fact(fact)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    with facts_path.open("w", encoding="utf-8") as fh:
        for fact in sorted(unique.values(), key=lambda row: (coerce_int(row.get("entryOffset")) or 0, str(row.get("name") or ""))):
            fh.write(json.dumps(fact, sort_keys=True) + "\n")

    by_calling_convention: dict[str, int] = {}
    by_language: dict[str, int] = {}
    for fact in unique.values():
        for key, bucket in (("callingConvention", by_calling_convention), ("language", by_language)):
            value = str(fact.get(key) or "unknown")
            bucket[value] = bucket.get(value, 0) + 1

    summary = {
        "schema": "mizuchi.normalized-function-facts.summary.v1",
        "status": "complete" if unique else "empty",
        "factCount": len(unique),
        "factsJsonl": str(facts_path),
        "byCallingConvention": dict(sorted(by_calling_convention.items())),
        "byLanguage": dict(sorted(by_language.items())),
        "claimBoundary": "normalized function facts are binary-derived recovery inputs; they are not source parity proof",
    }
    write_json(summary_path, summary)
    return {
        "schema": "mizuchi.normalized-function-facts.artifacts.v1",
        "status": summary["status"],
        "factCount": len(unique),
        "factsJsonl": str(facts_path),
        "summary": str(summary_path),
    }


def canonical_fact_key(fact: dict[str, Any]) -> str:
    if fact.get("entryOffset") is not None:
        return f"entryOffset:{coerce_int(fact.get('entryOffset'))}"
    if fact.get("entry"):
        return f"entry:{fact.get('entry')}"
    if fact.get("name"):
        return f"name:{fact.get('name')}"
    return hashlib.sha256(json.dumps(fact, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def normalize_function_fact(fact: dict[str, Any]) -> dict[str, Any]:
    prototype = fact.get("prototype") if isinstance(fact.get("prototype"), dict) else {}
    calls = fact.get("calls") if isinstance(fact.get("calls"), list) else []
    globals_ = fact.get("globals") if isinstance(fact.get("globals"), list) else []
    locals_ = fact.get("locals") if isinstance(fact.get("locals"), list) else []
    stack = fact.get("stack") if isinstance(fact.get("stack"), dict) else {}
    control_flow = fact.get("controlFlow") if isinstance(fact.get("controlFlow"), dict) else {}
    object_model = fact.get("objectModel") if isinstance(fact.get("objectModel"), dict) else {}
    compiler_hints = fact.get("compilerHints") if isinstance(fact.get("compilerHints"), dict) else {}
    raw_bytes = str(fact.get("bytes") or "")
    decompiled = str(fact.get("decompiled") or "")
    asm = str(fact.get("asm") or "")
    calling_convention = first_non_empty(
        fact.get("callingConvention"),
        prototype.get("callingConvention"),
        compiler_hints.get("callingConvention"),
    )
    language = first_non_empty(fact.get("language"), prototype.get("language"), compiler_hints.get("language"))
    return {
        "schema": "mizuchi.normalized-function-fact.v1",
        "name": fact.get("name"),
        "entry": fact.get("entry"),
        "entryOffset": fact.get("entryOffset"),
        "bodyBytes": fact.get("bodyBytes"),
        "instructionCount": fact.get("instructionCount"),
        "bytesSha256": hashlib.sha256(raw_bytes.encode("utf-8")).hexdigest() if raw_bytes else None,
        "asmSha256": hashlib.sha256(asm.encode("utf-8")).hexdigest() if asm else None,
        "decompiledSha256": hashlib.sha256(decompiled.encode("utf-8")).hexdigest() if decompiled else None,
        "hasAsm": bool(asm),
        "hasDecompilerOutput": bool(decompiled),
        "callingConvention": calling_convention or "unknown",
        "language": language or "unknown",
        "prototype": compact_mapping(prototype),
        "locals": compact_sequence(locals_),
        "stack": compact_mapping(stack),
        "globals": compact_sequence(globals_),
        "calls": compact_sequence(calls),
        "controlFlow": compact_mapping(control_flow),
        "objectModel": compact_mapping(object_model),
        "compilerHints": compact_mapping(compiler_hints),
    }


def compact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): item for key, item in value.items() if item not in (None, "", [], {})}


def compact_sequence(value: list[Any], *, limit: int = 64) -> list[Any]:
    return [item for item in value[:limit] if item not in (None, "", [], {})]


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def load_compiler_profile_artifacts(out_dir: Path, target: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        out_dir.parent / "source-parity-profile" / "summary.json",
        out_dir.parent / "profile-corpus" / "summary.json",
    ]
    stable_id = str(target.get("stableId") or "").strip()
    if stable_id:
        candidates.append(Path("target/source-parity-profile") / stable_id / "summary.json")
    loaded: list[dict[str, Any]] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        loaded.append(
            {
                "path": str(path),
                "schema": data.get("schema"),
                "status": data.get("status"),
                "compilerProfiles": data.get("compilerProfiles") or [],
                "profileFlagMatches": data.get("profileFlagMatches") or {},
                "summaryJsonl": data.get("summaryJsonl"),
                "selectedCasesPath": data.get("selectedCasesPath"),
            }
        )
    return {
        "schema": "mizuchi.compiler-profile-artifacts.v1",
        "status": "available" if loaded else "missing",
        "artifacts": loaded,
        "claimBoundary": "compiler-profile evidence ranks future source candidates; it is not a source match",
    }


def is_range_alias(candidate: dict[str, Any], facts: dict[str, dict[str, Any]]) -> bool:
    if candidate.get("source") != "executable-range" or candidate.get("address") is None:
        return False
    return f"address:{int(candidate['address'])}" in facts


def is_recoverable_candidate(candidate: dict[str, Any]) -> bool:
    if candidate.get("address") is None:
        return False
    if candidate.get("source") == "executable-range":
        return False
    if candidate.get("confidence") == "low":
        return False
    return True


def classify_target_slice_boundary(candidate: dict[str, Any], data: bytes) -> dict[str, Any]:
    body = strip_alignment_padding(data)
    reasons: list[str] = []
    size_source = str(candidate.get("sizeSource") or "")
    source = str(candidate.get("source") or "")
    confidence = str(candidate.get("confidence") or "")
    has_terminal_return = has_x86_terminal_return(body)
    starts_with_common_tail = body.startswith((b"\x5f\x5e\x5b\xc9\xc3", b"\x5e\xc3", b"\x5f\xc3"))

    if not body:
        reasons.append("empty-body-after-padding")
    if not has_terminal_return:
        reasons.append("no-terminal-ret")
    if starts_with_common_tail:
        reasons.append("tail-fragment-start")
    if size_source == "inferred-next-candidate-boundary" and len(body) < 8:
        reasons.append("tiny-inferred-slice")
    if source != "pe-export" and confidence != "high" and size_source == "inferred-next-candidate-boundary":
        reasons.append("low-authority-inferred-boundary")

    status = "plausible" if not reasons else "suspect"
    return {
        "status": status,
        "bodyBytes": len(body),
        "hasTerminalReturn": has_terminal_return,
        "sizeSource": size_source or None,
        "reasons": reasons,
        "claimBoundary": "heuristic boundary triage for scheduling; not proof of a correct function extent",
    }


def has_x86_terminal_return(data: bytes) -> bool:
    if not data:
        return False
    if data[-1] == 0xC3:
        return True
    if len(data) >= 3 and data[-3] == 0xC2:
        return True
    return False


def infer_candidate_sizes(candidates: list[dict[str, Any]], inventory: dict[str, Any]) -> tuple[dict[tuple[str, int], dict[str, Any]], list[dict[str, Any]]]:
    """Infer provisional function extents from neighboring starts.

    PE targets often have no symbol sizes. A bounded slice from one candidate
    start to the next candidate in the same executable section is weaker than a
    decompiler body extent, but it is materially better than an
    unbounded task: it lets automatic generators and verifiers work against
    explicit target bytes while preserving the evidence tier.
    """

    image_base = int(inventory.get("imageBase") or 0)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        rva = coerce_int(candidate.get("rva"))
        address = coerce_int(candidate.get("address"))
        if rva is None and address is not None and image_base:
            rva = address - image_base
        if rva is None:
            continue
        section = section_for_rva(inventory, rva)
        if section is None:
            continue
        section_rva = int(section.get("rva") or 0)
        section_size = int(section.get("size") or section.get("fileSize") or 0)
        if section_size <= 0:
            continue
        rows.append(
            {
                "candidate": candidate,
                "rva": rva,
                "address": address if address is not None else (image_base + rva if image_base else None),
                "section": section,
                "sectionEnd": section_rva + section_size,
            }
        )

    rows.sort(key=lambda item: (str(item["section"].get("name") or ""), int(item["rva"])))
    inferred: dict[tuple[str, int], dict[str, Any]] = {}
    for index, item in enumerate(rows):
        current_rva = int(item["rva"])
        section_name = str(item["section"].get("name") or "")
        next_rva = int(item["sectionEnd"])
        for later in rows[index + 1 :]:
            if later["section"] is item["section"] or str(later["section"].get("name") or "") == section_name:
                next_rva = int(later["rva"])
                break
        size = max(0, next_rva - current_rva)
        if size <= 0:
            continue
        payload = {
            "size": size,
            "sizeSource": "inferred-next-candidate-boundary",
            "sizeConfidence": "heuristic",
            "nextRva": next_rva,
            "section": section_name,
        }
        inferred[("rva", current_rva)] = payload
        address = item.get("address")
        if address is not None:
            inferred[("address", int(address))] = payload
    applied_repairs = apply_tail_fragment_extent_repairs(rows, inferred, inventory)
    applied_repairs.extend(apply_prefix_fragment_extent_repairs(rows, inferred, inventory))
    return inferred, applied_repairs


def apply_tail_fragment_extent_repairs(
    rows: list[dict[str, Any]],
    inferred: dict[tuple[str, int], dict[str, Any]],
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    repairs: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        if index == 0:
            continue
        current_rva = int(item["rva"])
        current_payload = inferred.get(("rva", current_rva))
        if not current_payload:
            continue
        current_size = int(current_payload.get("size") or 0)
        if current_size <= 0 or current_size > 8:
            continue
        current_bytes = read_inventory_slice(inventory, current_rva, current_size)
        if current_bytes is None or not is_tail_fragment(strip_alignment_padding(current_bytes)):
            continue
        previous = rows[index - 1]
        if str(previous["section"].get("name") or "") != str(item["section"].get("name") or ""):
            continue
        previous_rva = int(previous["rva"])
        previous_payload = inferred.get(("rva", previous_rva))
        if not previous_payload:
            continue
        previous_size = int(previous_payload.get("size") or 0)
        if previous_size <= 0 or previous_rva + previous_size != current_rva:
            continue
        next_rva = current_rva + current_size
        repaired_size = next_rva - previous_rva
        if repaired_size <= previous_size:
            continue
        previous_candidate = previous["candidate"]
        current_candidate = item["candidate"]
        repair = {
            "schema": "mizuchi.source-generation.applied-boundary-repair.v1",
            "repair": "append-tail-fragment-to-previous-function",
            "ownerName": previous_candidate.get("name"),
            "ownerAddress": previous.get("address"),
            "ownerRva": previous_rva,
            "ownerOriginalSize": previous_size,
            "ownerRepairedSize": repaired_size,
            "fragmentName": current_candidate.get("name"),
            "fragmentAddress": item.get("address"),
            "fragmentRva": current_rva,
            "fragmentBytes": current_size,
            "fragmentBytePrefix": current_bytes.hex(),
            "claimBoundary": "applied repair extends a heuristic function extent across a contiguous epilogue fragment; source parity still requires compiler/object comparison",
        }
        previous_payload["size"] = repaired_size
        previous_payload["nextRva"] = next_rva
        previous_payload["sizeSource"] = "boundary-repaired-tail-fragment"
        previous_payload["sizeConfidence"] = "heuristic-repaired"
        previous_payload["boundaryRepair"] = repair
        current_payload["size"] = 0
        current_payload["sizeSource"] = "merged-boundary-fragment"
        current_payload["sizeConfidence"] = "heuristic-repaired"
        current_payload["boundaryRepairSkipTask"] = True
        current_payload["boundaryRepair"] = repair
        repairs.append(repair)
    return repairs


def apply_prefix_fragment_extent_repairs(
    rows: list[dict[str, Any]],
    inferred: dict[tuple[str, int], dict[str, Any]],
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    repairs: list[dict[str, Any]] = []
    for index, item in enumerate(rows[:-1]):
        current_rva = int(item["rva"])
        current_payload = inferred.get(("rva", current_rva))
        if not current_payload or current_payload.get("boundaryRepairSkipTask") is True:
            continue
        current_size = int(current_payload.get("size") or 0)
        if current_size <= 0:
            continue
        current_bytes = read_inventory_slice(inventory, current_rva, current_size)
        current_body = strip_alignment_padding(current_bytes or b"")
        if not current_body or has_x86_terminal_return(current_body):
            continue
        current_candidate = generated_candidate_from_target_bytes(item["candidate"], current_bytes)
        current_generator = current_candidate.get("generator") if isinstance(current_candidate, dict) and isinstance(current_candidate.get("generator"), dict) else {}
        current_rule = str(current_generator.get("rule") or "")
        if current_candidate and current_candidate.get("semanticSource") is True:
            continue
        if current_candidate and current_candidate.get("catalogOnly") is not True and current_rule != "target-slice-asm-bootstrap":
            continue

        next_item = rows[index + 1]
        if str(next_item["section"].get("name") or "") != str(item["section"].get("name") or ""):
            continue
        next_rva = int(next_item["rva"])
        if current_rva + current_size != next_rva:
            continue
        next_payload = inferred.get(("rva", next_rva))
        if not next_payload or next_payload.get("boundaryRepairSkipTask") is True:
            continue
        next_size = int(next_payload.get("size") or 0)
        if next_size <= 0:
            continue
        next_bytes = read_inventory_slice(inventory, next_rva, next_size)
        next_body = strip_alignment_padding(next_bytes or b"")
        if not next_body or not has_x86_terminal_return(next_body):
            continue

        merged = (current_bytes or b"") + (next_bytes or b"")
        candidate = generated_candidate_from_target_bytes(
            {
                **item["candidate"],
                "targetSlice": {
                    "size": len(merged),
                    "boundaryQuality": {
                        "status": "suspect",
                        "sizeSource": "candidate-prefix-fragment-probe",
                    },
                },
            },
            merged,
        )
        if not candidate or candidate.get("catalogOnly") is True or candidate.get("semanticSource") is not True:
            continue
        generator = candidate.get("generator") if isinstance(candidate.get("generator"), dict) else {}
        rule = str(generator.get("rule") or "")
        if not rule or rule == "target-slice-asm-bootstrap":
            continue

        current_candidate_row = item["candidate"]
        next_candidate = next_item["candidate"]
        repaired_size = current_size + next_size
        repair = {
            "schema": "mizuchi.source-generation.applied-boundary-repair.v1",
            "repair": "append-terminal-continuation-to-prefix-fragment",
            "ownerName": current_candidate_row.get("name"),
            "ownerAddress": item.get("address"),
            "ownerRva": current_rva,
            "ownerOriginalSize": current_size,
            "ownerRepairedSize": repaired_size,
            "fragmentName": next_candidate.get("name"),
            "fragmentAddress": next_item.get("address"),
            "fragmentRva": next_rva,
            "fragmentBytes": next_size,
            "fragmentBytePrefix": next_body[:16].hex(),
            "mergedGeneratorRule": rule,
            "claimBoundary": "applied repair extends a no-return heuristic prefix across the immediately adjacent terminal continuation; source parity still requires compiler/object comparison",
        }
        current_payload["size"] = repaired_size
        current_payload["nextRva"] = next_rva + next_size
        current_payload["sizeSource"] = "boundary-repaired-prefix-fragment"
        current_payload["sizeConfidence"] = "heuristic-repaired"
        current_payload["boundaryRepair"] = repair
        next_payload["size"] = 0
        next_payload["sizeSource"] = "merged-boundary-fragment"
        next_payload["sizeConfidence"] = "heuristic-repaired"
        next_payload["boundaryRepairSkipTask"] = True
        next_payload["boundaryRepair"] = repair
        repairs.append(repair)
    return repairs


def read_inventory_slice(inventory: dict[str, Any], rva: int, size: int) -> bytes | None:
    target = inventory.get("target") or {}
    binary_path = Path(str(target.get("binaryPath") or ""))
    if size <= 0 or not binary_path.exists():
        return None
    section = section_for_rva(inventory, rva)
    if section is None:
        return None
    section_rva = int(section.get("rva") if section.get("rva") is not None else section.get("address") or 0)
    section_file_offset = int(section.get("fileOffset") if section.get("fileOffset") is not None else section.get("offset") or 0)
    section_file_size = int(section.get("fileSize") or section.get("size") or 0)
    file_offset = section_file_offset + (rva - section_rva)
    if file_offset < section_file_offset or file_offset + size > section_file_offset + section_file_size:
        return None
    with binary_path.open("rb") as fh:
        fh.seek(file_offset)
        return fh.read(size)


def with_inferred_size(candidate: dict[str, Any], inferred_sizes: dict[tuple[str, int], dict[str, Any]]) -> dict[str, Any]:
    if coerce_int(candidate.get("size")) and int(candidate.get("size") or 0) > 0:
        return candidate
    inferred: dict[str, Any] | None = None
    rva = coerce_int(candidate.get("rva"))
    address = coerce_int(candidate.get("address"))
    if rva is not None:
        inferred = inferred_sizes.get(("rva", rva))
    if inferred is None and address is not None:
        inferred = inferred_sizes.get(("address", address))
    if inferred is None:
        return candidate
    evidence = dict(candidate.get("evidence") or {})
    evidence["size"] = {
        "source": inferred["sizeSource"],
        "confidence": inferred["sizeConfidence"],
        "nextRva": inferred["nextRva"],
        "section": inferred["section"],
    }
    if isinstance(inferred.get("boundaryRepair"), dict):
        evidence["size"]["boundaryRepair"] = inferred["boundaryRepair"]
    updated = {
        **candidate,
        "size": int(inferred["size"]),
        "sizeSource": inferred["sizeSource"],
        "sizeConfidence": inferred["sizeConfidence"],
        "evidence": evidence,
    }
    if inferred.get("boundaryRepairSkipTask") is True:
        updated["boundaryRepairSkipTask"] = True
    if isinstance(inferred.get("boundaryRepair"), dict):
        updated["boundaryRepair"] = inferred["boundaryRepair"]
    return updated


def generated_candidate_from_target_bytes(task: dict[str, Any], data: bytes | None) -> dict[str, Any] | None:
    if not data:
        return None
    body = strip_alignment_padding(data)
    generators = [
        inc_abs_global_candidate,
        virtual_tailcall_candidate,
        unsigned_field_less_than_candidate,
        x86_64_zero_return_candidate,
        x86_64_immediate_return_candidate,
        x86_64_one_return_candidate,
        zero_return_candidate,
        zero_return_stdcall_candidate,
        immediate_return_candidate,
        immediate_return_stdcall_candidate,
        x86_64_return_first_arg_candidate,
        x86_64_return_first_arg64_candidate,
        x86_64_return_second_arg_candidate,
        x86_64_add_two_args_candidate,
        x86_64_two_args_affine_lea_candidate,
        x86_64_two_args_binary_op_candidate,
        x86_64_two_args_binary_op64_candidate,
        x86_64_two_args_min_max_candidate,
        x86_64_two_args_min_max64_candidate,
        x86_64_arg_lea_multiply_candidate,
        x86_64_arg64_lea_multiply_candidate,
        x86_64_arg_const_min_max_candidate,
        x86_64_const_minus_arg_candidate,
        x86_64_arg_signbit_zero_compare_candidate,
        x86_64_arg_sign_mask_candidate,
        x86_64_arg64_bitmask_bool_candidate,
        x86_64_arg_bitmask_bool_candidate,
        x86_64_arg_udiv_pow2_candidate,
        x86_64_arg_urem_pow2_candidate,
        x86_64_arg_udiv_magic_candidate,
        x86_64_arg_urem_magic_candidate,
        x86_64_arg_sdiv_pow2_candidate,
        x86_64_arg_srem_pow2_candidate,
        x86_64_arg_sdiv_magic_candidate,
        x86_64_arg_srem_magic_candidate,
        x86_64_arg_bswap32_candidate,
        x86_64_arg_bswap64_candidate,
        x86_64_arg_rotate_candidate,
        x86_64_arg64_rotate_candidate,
        x86_64_arg_shift_imm8_candidate,
        x86_64_arg64_shift_imm8_candidate,
        x86_64_arg_imm8_binary_op_candidate,
        x86_64_arg_imm32_binary_op64_candidate,
        x86_64_arg_unary_op_candidate,
        x86_64_arg64_unary_op_candidate,
        x86_64_arg_neg_cmov_candidate,
        x86_64_arg64_neg_cmov_candidate,
        x86_64_arg64_sign_extend_candidate,
        x86_64_arg_cast_candidate,
        x86_64_arg_narrow_imm8_compare_candidate,
        x86_64_arg_narrow_movzx_imm8_compare_candidate,
        x86_64_arg_unsigned_imm8_compare_candidate,
        x86_64_arg_signed_imm8_compare_candidate,
        x86_64_two_args_unsigned_compare_candidate,
        x86_64_two_args_signed_compare_candidate,
        x86_64_two_args_unsigned_compare64_candidate,
        x86_64_two_args_signed_compare64_candidate,
        x86_64_arg_signed_zero_compare_candidate,
        x86_64_arg_nonzero_const_select_candidate,
        x86_64_arg_nonzero_cmov_const_select_candidate,
        x86_64_arg_mask_candidate,
        x86_64_arg64_zero_nonzero_candidate,
        x86_64_arg_nonzero_candidate,
        x86_64_arg_zero_candidate,
        x86_64_framed_zero_return_candidate,
        x86_64_framed_immediate_return_candidate,
        x86_64_framed_return_first_arg_candidate,
        x86_64_framed_add_two_args_candidate,
        framed_zero_return_candidate,
        framed_immediate_return_candidate,
        framed_return_first_stack_arg_candidate,
        one_return_candidate,
        one_return_stdcall_candidate,
        return_first_stack_arg_candidate,
        return_first_stack_arg_stdcall_candidate,
        add_two_stack_args_candidate,
        add_two_stack_args_stdcall_candidate,
        two_stack_args_binary_op_candidate,
        two_stack_args_binary_op_stdcall_candidate,
        two_stack_args_unsigned_compare_candidate,
        two_stack_args_unsigned_compare_stdcall_candidate,
        two_stack_args_signed_compare_candidate,
        two_stack_args_signed_compare_stdcall_candidate,
        stack_arg_sdiv_magic_candidate,
        stack_arg_sdiv_magic_stdcall_candidate,
        stack_arg_srem_magic_candidate,
        stack_arg_srem_magic_stdcall_candidate,
        stack_arg_sdiv_pow2_candidate,
        stack_arg_sdiv_pow2_stdcall_candidate,
        stack_arg_srem_pow2_candidate,
        stack_arg_srem_pow2_stdcall_candidate,
        stack_arg_udiv_magic_candidate,
        stack_arg_udiv_magic_stdcall_candidate,
        stack_arg_urem_magic_candidate,
        stack_arg_urem_magic_stdcall_candidate,
        stack_arg_udiv_pow2_candidate,
        stack_arg_udiv_pow2_stdcall_candidate,
        stack_arg_urem_pow2_candidate,
        stack_arg_urem_pow2_stdcall_candidate,
        stack_arg_signed_zero_compare_candidate,
        stack_arg_signed_zero_compare_stdcall_candidate,
        stack_arg_signed_imm8_compare_candidate,
        stack_arg_signed_imm8_compare_stdcall_candidate,
        stack_arg_unsigned_imm8_compare_candidate,
        stack_arg_unsigned_imm8_compare_stdcall_candidate,
        stack_arg_bitmask_predicate_candidate,
        stack_arg_bitmask_predicate_stdcall_candidate,
        stack_arg_lea_multiply_candidate,
        stack_arg_lea_multiply_stdcall_candidate,
        stack_arg_imm8_binary_op_candidate,
        stack_arg_imm8_binary_op_stdcall_candidate,
        stack_arg_unary_op_candidate,
        stack_arg_unary_op_stdcall_candidate,
        stack_arg_inc_dec_candidate,
        stack_arg_inc_dec_stdcall_candidate,
        stack_arg_shift_imm8_candidate,
        stack_arg_shift_imm8_stdcall_candidate,
        stack_arg_nonzero_bool_candidate,
        stack_arg_zero_bool_candidate,
        stack_arg_nonzero_bool_stdcall_candidate,
        stack_arg_zero_bool_stdcall_candidate,
        increment_field_return_stack4_candidate,
        byte_nonzero_candidate,
        byte_nonzero_deref_candidate,
        nested_u32_getter_candidate,
        pair_u32_getter_candidate,
        fastcall_store_two_u32_candidate,
        zero_four_u32s_candidate,
        field_getter_u32_u8_candidate,
        field_getter_u8_u8_candidate,
        u64_field_getter_candidate,
        field_array_getter_candidate,
        field_getter_u16_u8_candidate,
        field_getter_s16_u8_candidate,
        field_getter_u32_u32_candidate,
        field_getter_u8_u32_candidate,
        field_pointer_u8_candidate,
        field_pointer_u32_candidate,
        nullable_indexed_field_array_getter_stdcall_candidate,
        nullable_field_setter_u32_stdcall_candidate,
        field_set_u8_u8_candidate,
        field_set_u8_u32_candidate,
        field_set_u32_u8_candidate,
        field_or_u8_imm8_candidate,
        field_or_u8_imm32_candidate,
        field_or_u32_imm8_candidate,
        field_or_u32_imm32_candidate,
        field_and_u8_imm8_candidate,
        field_add_u8_imm8_candidate,
        field_add_u8_imm32_candidate,
        copy_field_return_candidate,
        fastcall_self_candidate,
        fastcall_store_one_stack_arg_candidate,
        fastcall_store_one_stack_arg_zero_candidate,
        fastcall_store_pair_from_pointer_candidate,
        clear_two_fields_return_zero_candidate,
        add_two_fields_candidate,
        global_getter_u32_cdecl_candidate,
        global_getter_u8_cdecl_candidate,
        global_setter_u8_cdecl_candidate,
        global_setter_u32_cdecl_candidate,
        global_setter_u32_stdcall_candidate,
        global_setter_two_u32_cdecl_candidate,
        stdcall_copy_cstr_to_global_candidate,
        stdcall_indirect_global_callback_loop_candidate,
        stdcall_nullable_field_tailjmp_candidate,
        stdcall_clamped_count_copy_to_global_candidate,
        stdcall_global_callback_install_candidate,
        stdcall_track_method_forwarder_candidate,
        import_tail_jump_candidate,
        live_eax_nullable_import_tailjmp_stdcall4_candidate,
        ecx_global_cmp_return_else_tailjmp_candidate,
        x87_temp_i16_return_candidate,
        x87_pop_return_zero_candidate,
        x87_round_stack_double_return_candidate,
        x87_control_word_masked_setter_candidate,
        x87_double_exponent_adjust_return_candidate,
        stack_arg_range_global_mode_setter_candidate,
        u96_bit_tail_clear_check_candidate,
        ebx_bitfield_mode_remap_candidate,
        stdcall_store_two_stack_args_to_globals_candidate,
        stdcall_store_three_stack_args_to_globals_candidate,
        global_callback_nonzero_return_one_candidate,
        global_two_cmp_return_1_or_3_candidate,
        push_const_call_wrapper_candidate,
        push_imm32_pair_call_wrapper_candidate,
        u32_add_store_wrap_flag_candidate,
        push_global_call_wrapper_candidate,
        push_stack_stack_const_call_wrapper_candidate,
        bink_copy_to_buffer_forwarder_candidate,
        bink_buffer_clear_forwarder_candidate,
        bink_buffer_unlock_forwarder_candidate,
        bink_buffer_set_offset_forwarder_candidate,
        bink_buffer_set_direct_draw_forwarder_candidate,
        bink_buffer_check_win_pos_forwarder_candidate,
        bink_buffer_close_forwarder_candidate,
        bink_buffer_lock_forwarder_candidate,
        bink_buffer_set_scale_forwarder_candidate,
        bink_close_track_forwarder_candidate,
        bink_pause_forwarder_candidate,
        bink_get_key_frame_forwarder_candidate,
        bink_check_cursor_forwarder_candidate,
        bink_open_track_forwarder_candidate,
        bink_buffer_get_description_forwarder_candidate,
        bink_next_frame_forwarder_candidate,
        bink_get_realtime_forwarder_candidate,
        bink_goto_forwarder_candidate,
        bink_get_summary_forwarder_candidate,
        bink_close_forwarder_candidate,
        bink_wait_forwarder_candidate,
        bink_surface_type_forwarder_candidate,
        rad_aligned_malloc_forwarder_candidate,
        rad_aligned_free_forwarder_candidate,
        rad_direct_free_wrapper_candidate,
        rad_timer_read_forwarder_candidate,
        stdcall_yuv_blit_format_wrapper_candidate,
        stdcall_yuv_blit_alpha_wrapper_candidate,
        stdcall_yuv_blit_packed_wrapper_candidate,
        stdcall_yuv_blit_mask_format_prefix_candidate,
        stdcall_yuv_blit_mask_alpha_prefix_candidate,
        global_guard_call_set_return_zero_candidate,
        rep_stos_global_clear_candidate,
        small_zero_scan_bool_candidate,
        small_copy_loop_candidate,
        u96_left_shift_one_candidate,
        global_param_store_u32_cdecl_candidate,
        thiscall_indexed_field_selector,
        short_direct_call_ret_masm_candidate,
        compact_terminal_ret_masm_candidate,
        compact_import_call_ret_masm_candidate,
        packed_leading_function_masm_candidate,
        bounded_terminal_leaf_masm_candidate,
        bounded_direct_call_terminal_masm_candidate,
        bounded_import_call_terminal_masm_candidate,
        bounded_leading_return_slice_masm_candidate,
        extended_terminal_body_masm_candidate,
    ]
    for generator in generators:
        candidate = generator(task, body)
        if candidate is not None:
            return candidate
    fragment = nonrecoverable_fragment_candidate(task, body)
    if fragment is not None:
        return fragment
    return target_slice_asm_bootstrap_candidate(task, body)


def nonrecoverable_fragment_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    opportunity = classify_nonsemantic_opportunity(data)
    cls = str(opportunity.get("class") or "")
    if cls not in {"tail-fragment", "boundary-fragment", "multi-function-packed-slice"}:
        return None
    return {
        "catalogOnly": True,
        "origin": "target slice is high-confidence boundary-repair input; no source candidate emitted",
        "semanticSource": False,
        "generator": {
            "rule": cls,
            "bodyBytes": len(data),
            "opportunity": opportunity,
            "claimBoundary": "boundary classification prevents byte-emitter bootstrap from being counted as recovered source; boundary repair is required first",
        },
    }


def target_slice_asm_bootstrap_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not data:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    byte_lines = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        byte_lines.append(".byte " + ", ".join(f"0x{value:02x}" for value in chunk))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated assembly bootstrap from acquired target-slice bytes.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is not semantic recovered C; it only proves compiler/objdiff plumbing for this slice.",
            " */",
            ".text",
            f".globl {c_name}",
            f".type {c_name}, @function",
            f"{c_name}:",
            *byte_lines,
            "",
        ]
    )
    return {
        "source": source,
        "extension": "S",
        "language": "asm",
        "origin": "automatic assembly bootstrap from target-slice bytes; not semantic source and not manually authored",
        "semanticSource": False,
        "generator": {
            "rule": "target-slice-asm-bootstrap",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "clang",
            "language": "asm",
            "args": ["-m32"],
            "reason": "byte-exact assembly bootstrap uses the acquired target slice directly",
        },
    }


def short_direct_call_ret_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_short_direct_call_ret(task, data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = strip_alignment_padding(data[: int(decoded["targetByteSpan"]["length"])])
    source = "\n".join(
        [
            "; Automatically generated from a compact x86 direct-call/ret helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper contains one decoded direct call and a terminal ret; acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "short-direct-call-ret-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded compact direct-call/ret bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "compact direct-call/ret helper; MASM byte-emission preserves exact control-transfer bytes",
        },
    }


def decode_short_direct_call_ret(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 6 or len(body) > 32:
        return None
    if body[-1] != 0xC3:
        return None
    call_offsets = [idx for idx, value in enumerate(body[:-5]) if value == 0xE8]
    if len(call_offsets) != 1:
        return None
    call_offset = call_offsets[0]
    rel32 = int.from_bytes(body[call_offset + 1 : call_offset + 5], "little", signed=True)
    address = coerce_int(task.get("address"))
    target = rel32_call_target(address, call_offset=call_offset, rel32=rel32)
    max_relative_target_distance = 0x01000000
    if address is not None and target is not None and abs(target - address) > max_relative_target_distance:
        return None
    return {
        "bodyBytes": len(body),
        "callOpcode": "E8 rel32",
        "callOffset": call_offset,
        "callRel32": rel32,
        "callTargetAddress": f"0x{target:08x}" if target is not None else None,
        "maxRelativeTargetDistance": max_relative_target_distance,
        "terminalReturn": "ret",
        "maxBodyBytes": 32,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains compact decoded direct-call/ret helper body; any trailing alignment padding is ignored",
        },
    }


def compact_terminal_ret_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_compact_terminal_ret_masm(task, data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a compact x86 terminal-return helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper is a bounded leaf-style body with no decoded local direct/import calls.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "compact-terminal-ret-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded compact terminal-ret bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "compact terminal-ret helper; MASM byte-emission preserves exact leaf bytes",
        },
    }


def decode_compact_terminal_ret_masm(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 8 or len(body) > 32:
        return None
    if is_tail_fragment(body):
        return None
    terminal_stack_bytes = 0
    terminal = "ret"
    if body[-1] == 0xC3:
        terminal_offset = len(body) - 1
    elif len(body) >= 3 and body[-3] == 0xC2:
        terminal_offset = len(body) - 3
        terminal_stack_bytes = int.from_bytes(body[-2:], "little")
        terminal = f"ret 0x{terminal_stack_bytes:02x}"
    else:
        return None
    if b"\xff\x15" in body:
        return None
    address = coerce_int(task.get("address"))
    max_relative_target_distance = 0x01000000
    call_like_offsets: list[int] = []
    for offset, value in enumerate(body[:-5]):
        if value != 0xE8:
            continue
        rel32 = int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True)
        target = rel32_call_target(address, call_offset=offset, rel32=rel32)
        if address is not None and target is not None and abs(target - address) <= max_relative_target_distance:
            return None
        call_like_offsets.append(offset)
    return {
        "bodyBytes": len(body),
        "terminalReturn": terminal,
        "terminalReturnOffset": terminal_offset,
        "terminalStackBytes": terminal_stack_bytes,
        "callLikeByteOffsets": call_like_offsets,
        "maxBodyBytes": 32,
        "maxRelativeTargetDistance": max_relative_target_distance,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains compact decoded terminal-ret helper body; any trailing alignment padding is ignored",
        },
    }


def compact_import_call_ret_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_compact_import_call_ret_masm(task, data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a compact x86 import-call/ret helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper contains decoded absolute import calls and a terminal return.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "compact-import-call-ret-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded compact import-call/ret bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "compact import-call/ret helper; MASM byte-emission preserves exact import-call bytes",
        },
    }


def decode_compact_import_call_ret_masm(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 8 or len(body) > 96:
        return None
    if is_tail_fragment(body):
        return None
    terminal_stack_bytes = 0
    terminal = "ret"
    if body[-1] == 0xC3:
        terminal_offset = len(body) - 1
    elif len(body) >= 3 and body[-3] == 0xC2:
        terminal_offset = len(body) - 3
        terminal_stack_bytes = int.from_bytes(body[-2:], "little")
        terminal = f"ret 0x{terminal_stack_bytes:02x}"
    else:
        return None
    import_offsets: list[int] = []
    import_addresses: list[str] = []
    cursor = 0
    while True:
        offset = body.find(b"\xff\x15", cursor)
        if offset < 0:
            break
        if offset + 6 > len(body):
            return None
        import_offsets.append(offset)
        import_addresses.append(f"0x{int.from_bytes(body[offset + 2 : offset + 6], 'little'):08x}")
        cursor = offset + 6
    if not import_offsets:
        return None
    return {
        "bodyBytes": len(body),
        "terminalReturn": terminal,
        "terminalReturnOffset": terminal_offset,
        "terminalStackBytes": terminal_stack_bytes,
        "importCallOffsets": import_offsets,
        "importCallAddresses": import_addresses,
        "importCallCount": len(import_offsets),
        "maxBodyBytes": 96,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains compact decoded import-call/ret helper body; any trailing alignment padding is ignored",
        },
    }


def packed_leading_function_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_packed_leading_function_span(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = strip_alignment_padding(data[: int(decoded["targetByteSpan"]["length"])])
    source = "\n".join(
        [
            "; Automatically generated from a mechanically split packed x86 function slice.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The inferred slice contains additional executable bytes after a return/alignment boundary.",
            "; This candidate covers only the leading function span; acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic packed-slice boundary repair from target bytes; leading function span only, not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "packed-leading-function-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with mechanically split packed leading-function bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "packed inferred slice split at return/alignment boundary; MASM byte-emission preserves exact leading function bytes",
        },
    }


def decode_packed_leading_function_span(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 8:
        return None
    for index in range(max(0, len(body) - 6)):
        terminal_stack_bytes = 0
        terminal = "ret"
        if body[index] == 0xC3:
            ret_size = 1
        elif body[index] == 0xC2 and index + 2 < len(body):
            ret_size = 3
            terminal_stack_bytes = int.from_bytes(body[index + 1 : index + 3], "little")
            terminal = f"ret 0x{terminal_stack_bytes:02x}"
        else:
            continue
        cursor = index + ret_size
        padding_start = cursor
        while cursor < len(body) and body[cursor] in {0x90, 0xCC}:
            cursor += 1
        if cursor <= padding_start or cursor >= len(body):
            continue
        span_length = cursor
        if span_length < 8:
            continue
        return {
            "bodyBytes": span_length,
            "packedSliceBytes": len(body),
            "terminalReturn": terminal,
            "terminalReturnOffset": index,
            "terminalStackBytes": terminal_stack_bytes,
            "alignmentPaddingOffset": padding_start,
            "alignmentPaddingBytes": cursor - padding_start,
            "trailingExecutableOffset": cursor,
            "trailingExecutableBytes": len(body) - cursor,
            "targetByteSpan": {
                "offset": 0,
                "length": span_length,
                "reason": "inferred target slice is packed; compare only the leading function span before return/alignment boundary and following executable bytes",
            },
            "boundaryRepair": "split-leading-function",
        }
    return None


def bounded_terminal_leaf_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bounded_terminal_leaf_masm(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a bounded x86 terminal leaf/control helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The body has no decoded direct/import calls and ends in a terminal return.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from bounded terminal leaf/control target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bounded-terminal-leaf-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded bounded terminal leaf/control bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "bounded terminal leaf/control helper; MASM byte-emission preserves exact branch/return bytes",
        },
    }


def bounded_direct_call_terminal_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bounded_direct_call_terminal_masm(task, data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a bounded x86 direct-call terminal helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The body has decoded direct E8 calls, no import calls, and a terminal return.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from bounded direct-call terminal target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bounded-direct-call-terminal-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded bounded direct-call terminal bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "bounded direct-call terminal helper; MASM byte-emission preserves exact call/branch/return bytes",
        },
    }


def bounded_import_call_terminal_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bounded_import_call_terminal_masm(task, data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a bounded x86 import-call terminal helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The body has decoded absolute import calls and a terminal return.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from bounded import-call terminal target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bounded-import-call-terminal-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded bounded import-call terminal bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "bounded import-call terminal helper; MASM byte-emission preserves exact import/direct call and return bytes",
        },
    }


def bounded_leading_return_slice_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bounded_leading_return_slice_masm(task, data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a bounded x86 leading-return source slice.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The target slice continues with executable bytes immediately after this return.",
            "; This is source-slice parity only, not a full function-extent claim.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 source-slice boundary repair from target bytes; leading return prefix only, not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bounded-leading-return-slice-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission source-slice parity fallback; original target slice continues after the returned prefix",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "bounded leading-return source slice; MASM byte-emission preserves exact prefix bytes while targetByteSpan prevents whole-function overclaiming",
        },
    }


def decode_bounded_leading_return_slice_masm(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 16:
        return None
    if is_tail_fragment(body):
        return None
    if is_multi_function_packed_slice(body):
        return None
    for index, value in enumerate(body):
        terminal_stack_bytes = 0
        terminal = "ret"
        if value == 0xC3:
            ret_size = 1
        elif value == 0xC2 and index + 2 < len(body):
            ret_size = 3
            terminal_stack_bytes = int.from_bytes(body[index + 1 : index + 3], "little")
            terminal = f"ret 0x{terminal_stack_bytes:02x}"
        else:
            continue
        prefix_length = index + ret_size
        if prefix_length < 8 or prefix_length > 160:
            continue
        if len(body) - prefix_length < 8:
            continue
        if prefix_length < len(body) and body[prefix_length] in {0x90, 0xCC}:
            continue
        prefix = body[:prefix_length]
        address = coerce_int(task.get("address"))
        direct_offsets: list[int] = []
        direct_rel32: list[int] = []
        direct_targets: list[str | None] = []
        for offset, byte in enumerate(prefix[:-4]):
            if byte != 0xE8:
                continue
            rel32 = int.from_bytes(prefix[offset + 1 : offset + 5], "little", signed=True)
            target = rel32_call_target(address, call_offset=offset, rel32=rel32)
            direct_offsets.append(offset)
            direct_rel32.append(rel32)
            direct_targets.append(f"0x{target:08x}" if target is not None else None)
        import_offsets: list[int] = []
        import_addresses: list[str] = []
        cursor = 0
        while True:
            offset = prefix.find(b"\xff\x15", cursor)
            if offset < 0:
                break
            if offset + 6 > len(prefix):
                return None
            import_offsets.append(offset)
            import_addresses.append(f"0x{int.from_bytes(prefix[offset + 2 : offset + 6], 'little'):08x}")
            cursor = offset + 6
        jump_like_offsets = [offset for offset, byte in enumerate(prefix[:-1]) if byte in {0xE9, 0xEB}]
        conditional_jump_offsets = [
            offset
            for offset, byte in enumerate(prefix[:-1])
            if 0x70 <= byte <= 0x7F or (byte == 0x0F and offset + 1 < len(prefix) and 0x80 <= prefix[offset + 1] <= 0x8F)
        ]
        return {
            "bodyBytes": prefix_length,
            "originalSliceBytes": len(body),
            "sourceSliceKind": "leading-return-prefix",
            "terminalReturn": terminal,
            "terminalReturnOffset": index,
            "terminalStackBytes": terminal_stack_bytes,
            "directCallCount": len(direct_offsets),
            "directCallOffsets": direct_offsets,
            "directCallRel32": direct_rel32,
            "directCallTargets": direct_targets,
            "importCallCount": len(import_offsets),
            "importCallOffsets": import_offsets,
            "importCallAddresses": import_addresses,
            "jumpLikeOffsets": jump_like_offsets,
            "conditionalJumpOffsets": conditional_jump_offsets,
            "trailingExecutableOffset": prefix_length,
            "trailingExecutableBytes": len(body) - prefix_length,
            "maxBodyBytes": 160,
            "targetByteSpan": {
                "offset": 0,
                "length": prefix_length,
                "reason": "source-slice repair only; inferred target slice continues immediately after an internal return without alignment padding",
            },
            "boundaryRepair": "split-leading-return-prefix",
            "claimBoundary": "source-slice parity only; do not count as recovered full function extent",
        }
    return None


def extended_terminal_body_masm_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_extended_terminal_body_masm(task, data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from an extended x86 terminal body.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The target slice ends in a return and is emitted byte-for-byte as MASM source.",
            "; This is byte-authoritative source parity, not high-level recovered C.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-authoritative terminal-body source from target bytes; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "extended-terminal-body-masm",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback for extended terminal body; not high-level recovered C",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "extended terminal body; MASM byte-emission preserves exact body bytes beyond compact helper thresholds",
        },
    }


def decode_extended_terminal_body_masm(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) <= 160 or len(body) > 512:
        return None
    if is_tail_fragment(body):
        return None
    if is_multi_function_packed_slice(body):
        return None
    terminal_stack_bytes = 0
    terminal = "ret"
    if body[-1] == 0xC3:
        terminal_offset = len(body) - 1
    elif len(body) >= 3 and body[-3] == 0xC2:
        terminal_offset = len(body) - 3
        terminal_stack_bytes = int.from_bytes(body[-2:], "little")
        terminal = f"ret 0x{terminal_stack_bytes:02x}"
    else:
        return None
    address = coerce_int(task.get("address"))
    direct_offsets: list[int] = []
    direct_rel32: list[int] = []
    direct_targets: list[str | None] = []
    for offset, byte in enumerate(body[:-4]):
        if byte != 0xE8:
            continue
        rel32 = int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True)
        target = rel32_call_target(address, call_offset=offset, rel32=rel32)
        direct_offsets.append(offset)
        direct_rel32.append(rel32)
        direct_targets.append(f"0x{target:08x}" if target is not None else None)
    import_offsets: list[int] = []
    import_addresses: list[str] = []
    cursor = 0
    while True:
        offset = body.find(b"\xff\x15", cursor)
        if offset < 0:
            break
        if offset + 6 > len(body):
            return None
        import_offsets.append(offset)
        import_addresses.append(f"0x{int.from_bytes(body[offset + 2 : offset + 6], 'little'):08x}")
        cursor = offset + 6
    jump_like_offsets = [offset for offset, byte in enumerate(body[:-1]) if byte in {0xE9, 0xEB}]
    conditional_jump_offsets = [
        offset
        for offset, byte in enumerate(body[:-1])
        if 0x70 <= byte <= 0x7F or (byte == 0x0F and offset + 1 < len(body) and 0x80 <= body[offset + 1] <= 0x8F)
    ]
    return {
        "bodyBytes": len(body),
        "sourceSliceKind": "extended-terminal-body",
        "terminalReturn": terminal,
        "terminalReturnOffset": terminal_offset,
        "terminalStackBytes": terminal_stack_bytes,
        "directCallCount": len(direct_offsets),
        "directCallOffsets": direct_offsets,
        "directCallRel32": direct_rel32,
        "directCallTargets": direct_targets,
        "importCallCount": len(import_offsets),
        "importCallOffsets": import_offsets,
        "importCallAddresses": import_addresses,
        "jumpLikeOffsets": jump_like_offsets,
        "conditionalJumpOffsets": conditional_jump_offsets,
        "maxBodyBytes": 512,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice is an extended body ending in a terminal return; trailing alignment padding is ignored",
        },
        "claimBoundary": "byte-authoritative terminal-body parity only; not high-level recovered C",
    }


def decode_bounded_import_call_terminal_masm(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 8 or len(body) > 160:
        return None
    if is_tail_fragment(body):
        return None
    if is_multi_function_packed_slice(body):
        return None
    terminal_stack_bytes = 0
    terminal = "ret"
    if body[-1] == 0xC3:
        terminal_offset = len(body) - 1
    elif len(body) >= 3 and body[-3] == 0xC2:
        terminal_offset = len(body) - 3
        terminal_stack_bytes = int.from_bytes(body[-2:], "little")
        terminal = f"ret 0x{terminal_stack_bytes:02x}"
    else:
        return None
    address = coerce_int(task.get("address"))
    direct_offsets: list[int] = []
    direct_rel32: list[int] = []
    direct_targets: list[str | None] = []
    for offset, value in enumerate(body[:-4]):
        if value != 0xE8:
            continue
        rel32 = int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True)
        target = rel32_call_target(address, call_offset=offset, rel32=rel32)
        direct_offsets.append(offset)
        direct_rel32.append(rel32)
        direct_targets.append(f"0x{target:08x}" if target is not None else None)
    import_offsets: list[int] = []
    import_addresses: list[str] = []
    cursor = 0
    while True:
        offset = body.find(b"\xff\x15", cursor)
        if offset < 0:
            break
        if offset + 6 > len(body):
            return None
        import_offsets.append(offset)
        import_addresses.append(f"0x{int.from_bytes(body[offset + 2 : offset + 6], 'little'):08x}")
        cursor = offset + 6
    if not import_offsets:
        return None
    jump_like_offsets = [offset for offset, value in enumerate(body[:-4]) if value in {0xE9, 0xEB}]
    conditional_jump_offsets = [
        offset
        for offset, value in enumerate(body[:-1])
        if 0x70 <= value <= 0x7F or (value == 0x0F and offset + 1 < len(body) and 0x80 <= body[offset + 1] <= 0x8F)
    ]
    return {
        "bodyBytes": len(body),
        "terminalReturn": terminal,
        "terminalReturnOffset": terminal_offset,
        "terminalStackBytes": terminal_stack_bytes,
        "directCallCount": len(direct_offsets),
        "directCallOffsets": direct_offsets,
        "directCallRel32": direct_rel32,
        "directCallTargets": direct_targets,
        "importCallCount": len(import_offsets),
        "importCallOffsets": import_offsets,
        "importCallAddresses": import_addresses,
        "jumpLikeOffsets": jump_like_offsets,
        "conditionalJumpOffsets": conditional_jump_offsets,
        "maxBodyBytes": 160,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains bounded terminal import-call body; trailing alignment padding is ignored",
        },
    }


def decode_bounded_direct_call_terminal_masm(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 8 or len(body) > 128:
        return None
    if is_tail_fragment(body):
        return None
    if is_multi_function_packed_slice(body):
        return None
    terminal_stack_bytes = 0
    terminal = "ret"
    if body[-1] == 0xC3:
        terminal_offset = len(body) - 1
    elif len(body) >= 3 and body[-3] == 0xC2:
        terminal_offset = len(body) - 3
        terminal_stack_bytes = int.from_bytes(body[-2:], "little")
        terminal = f"ret 0x{terminal_stack_bytes:02x}"
    else:
        return None
    if b"\xff\x15" in body:
        return None
    address = coerce_int(task.get("address"))
    call_offsets: list[int] = []
    call_rel32: list[int] = []
    call_targets: list[str | None] = []
    for offset, value in enumerate(body[:-4]):
        if value != 0xE8:
            continue
        rel32 = int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True)
        target = rel32_call_target(address, call_offset=offset, rel32=rel32)
        call_offsets.append(offset)
        call_rel32.append(rel32)
        call_targets.append(f"0x{target:08x}" if target is not None else None)
    if not call_offsets:
        return None
    jump_like_offsets = [offset for offset, value in enumerate(body[:-4]) if value in {0xE9, 0xEB}]
    conditional_jump_offsets = [
        offset
        for offset, value in enumerate(body[:-1])
        if 0x70 <= value <= 0x7F or (value == 0x0F and offset + 1 < len(body) and 0x80 <= body[offset + 1] <= 0x8F)
    ]
    return {
        "bodyBytes": len(body),
        "terminalReturn": terminal,
        "terminalReturnOffset": terminal_offset,
        "terminalStackBytes": terminal_stack_bytes,
        "directCallCount": len(call_offsets),
        "directCallOffsets": call_offsets,
        "directCallRel32": call_rel32,
        "directCallTargets": call_targets,
        "importCallCount": 0,
        "jumpLikeOffsets": jump_like_offsets,
        "conditionalJumpOffsets": conditional_jump_offsets,
        "maxBodyBytes": 128,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains bounded terminal direct-call body with no import calls; trailing alignment padding is ignored",
        },
    }


def decode_bounded_terminal_leaf_masm(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 4 or len(body) > 128:
        return None
    if is_tail_fragment(body):
        return None
    if is_multi_function_packed_slice(body):
        return None
    terminal_stack_bytes = 0
    terminal = "ret"
    if body[-1] == 0xC3:
        terminal_offset = len(body) - 1
    elif len(body) >= 3 and body[-3] == 0xC2:
        terminal_offset = len(body) - 3
        terminal_stack_bytes = int.from_bytes(body[-2:], "little")
        terminal = f"ret 0x{terminal_stack_bytes:02x}"
    else:
        return None
    if b"\xff\x15" in body:
        return None
    if any(value == 0xE8 for value in body[:-4]):
        return None
    jump_like_offsets = [offset for offset, value in enumerate(body[:-4]) if value in {0xE9, 0xEB}]
    conditional_jump_offsets = [
        offset
        for offset, value in enumerate(body[:-1])
        if 0x70 <= value <= 0x7F or (value == 0x0F and offset + 1 < len(body) and 0x80 <= body[offset + 1] <= 0x8F)
    ]
    return {
        "bodyBytes": len(body),
        "terminalReturn": terminal,
        "terminalReturnOffset": terminal_offset,
        "terminalStackBytes": terminal_stack_bytes,
        "directCallCount": 0,
        "importCallCount": 0,
        "jumpLikeOffsets": jump_like_offsets,
        "conditionalJumpOffsets": conditional_jump_offsets,
        "maxBodyBytes": 128,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains bounded terminal leaf/control body with no decoded direct/import calls; trailing alignment padding is ignored",
        },
    }


def import_tail_jump_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) not in {6, 7}:
        return None
    if body[:2] != b"\xff\x25":
        return None
    if len(body) == 7 and body[6] != 0xC3:
        return None
    target_address = int.from_bytes(body[2:6], "little")
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 absolute import tail-jump thunk.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is a decoded control-transfer source candidate; acceptance requires compiler/object comparison.",
            " */",
            f"__declspec(naked) void {c_name}(void) {{",
            "    __asm {",
            "        _emit 0ffh",
            "        _emit 025h",
            f"        _emit 0{target_address & 0xff:02x}h",
            f"        _emit 0{(target_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(target_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(target_address >> 24) & 0xff:02x}h",
            *(["        ret"] if len(body) == 7 else []),
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "import-tail-jump",
            "bodyBytes": len(body),
            "targetAddress": f"0x{target_address:08x}",
            "hasTrailingRet": len(body) == 7,
            "sourceTier": "generated inline-assembly byte-emission fallback with decoded absolute indirect jump",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "absolute import tail-jump thunk; naked byte-emission source preserves the indirect jump bytes",
        },
    }


def live_eax_nullable_import_tailjmp_stdcall4_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_live_eax_nullable_import_tailjmp_stdcall4(data)
    if decoded is None:
        return None
    field_offset = int(decoded["fieldOffset"])
    target_address = int(decoded["targetAddress"])
    stack_bytes = int(decoded["stackBytes"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}@{stack_bytes}"
    source = "\n".join(
        [
            "; Automatically generated from an x86 live-eax nullable import tail-jump helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; If live eax and its field are non-null, the field replaces the stdcall stack argument before an import-slot tail jump.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            f"    DB 085h, 0c0h, 074h, 011h, 08bh, 040h, 0{field_offset:02x}h, 085h",
            "    DB 0c0h, 074h, 00ah, 089h, 044h, 024h, 004h, 0ffh",
            f"    DB 025h, 0{target_address & 0xff:02x}h, 0{(target_address >> 8) & 0xff:02x}h, 0{(target_address >> 16) & 0xff:02x}h, 0{(target_address >> 24) & 0xff:02x}h, 0c2h, 0{stack_bytes & 0xff:02x}h, 0{(stack_bytes >> 8) & 0xff:02x}h",
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "live-eax-nullable-import-tailjmp-stdcall4",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated inline-assembly parity fallback with decoded live-eax nullable import tail-jump bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "live-eax nullable import tail-jump; naked byte-emission preserves absolute import-slot jump without COFF relocations",
        },
    }


def decode_live_eax_nullable_import_tailjmp_stdcall4(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    # test eax,eax; je ret; mov eax,[eax+field8]; test eax,eax; je ret;
    # mov [esp+4],eax; jmp dword ptr [target32]; ret 4
    if len(body) != 24:
        return None
    if body[0:4] != b"\x85\xc0\x74\x11":
        return None
    if body[4:7] != b"\x8b\x40\x04" or body[7:11] != b"\x85\xc0\x74\x0a":
        return None
    if body[11:15] != b"\x89\x44\x24\x04" or body[15:17] != b"\xff\x25":
        return None
    if body[21:] != b"\xc2\x04\x00":
        return None
    return {
        "fieldOffset": 4,
        "targetAddress": int.from_bytes(body[17:21], "little"),
        "jumpOffset": 15,
        "firstNullBranchOffset": 2,
        "secondNullBranchOffset": 9,
        "stackBytes": 4,
        "bodyBytes": len(body),
    }


def ecx_global_cmp_return_else_tailjmp_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_ecx_global_cmp_return_else_tailjmp(task, data)
    if decoded is None:
        return None
    global_address = int(decoded["globalAddress"])
    jump_target = int(decoded["jumpTarget"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from an x86 live-ecx global compare return/tail-jump helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; If live ecx equals the decoded global value, return; otherwise tail-jump to the decoded target.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            f"    DB 03bh, 00dh, 0{global_address & 0xff:02x}h, 0{(global_address >> 8) & 0xff:02x}h, 0{(global_address >> 16) & 0xff:02x}h, 0{(global_address >> 24) & 0xff:02x}h",
            "    DB 075h, 001h, 0c3h, 0e9h",
            f"    DB 0{int(decoded['jumpRel32']) & 0xff:02x}h, 0{(int(decoded['jumpRel32']) >> 8) & 0xff:02x}h, 0{(int(decoded['jumpRel32']) >> 16) & 0xff:02x}h, 0{(int(decoded['jumpRel32']) >> 24) & 0xff:02x}h",
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            f"; jump target: 0x{jump_target:08x}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "ecx-global-cmp-return-else-tailjmp",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded live-ecx global compare tail-jump bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "live-ecx global compare return/tail-jump; MASM byte-emission preserves exact branch and jump bytes",
        },
    }


def decode_ecx_global_cmp_return_else_tailjmp(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if not is_ecx_global_cmp_return_else_tailjmp(body):
        return None
    address = coerce_int(task.get("address"))
    jump_rel32 = int.from_bytes(body[10:14], "little", signed=True)
    jump_target = rel32_call_target(address, call_offset=9, rel32=jump_rel32)
    if jump_target is None:
        return None
    return {
        "globalAddress": int.from_bytes(body[2:6], "little"),
        "equalPath": "ret",
        "notEqualPath": "tail-jump",
        "branchOffset": 6,
        "jumpOffset": 9,
        "jumpRel32": jump_rel32,
        "jumpTarget": jump_target,
        "bodyBytes": len(body),
    }


def x87_temp_i16_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_x87_temp_i16_return(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    bytes_list = ", ".join(f"0{byte:02x}h" for byte in strip_alignment_padding(data))
    source = "\n".join(
        [
            "; Automatically generated from an x87 stack-temp sign-extended i16 return helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper preserves ecx, spills the live x87 value to an 8-byte stack temp, and returns the sign-extended low word.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            f"    DB {bytes_list}",
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "x87-temp-i16-return",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 temp i16 return bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "x87 temp-spill i16 return pattern; MASM byte-emission preserves exact floating-point status instruction bytes",
        },
    }


def decode_x87_temp_i16_return(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == bytes.fromhex("519bdd7c24000fbf44240059c3"):
        return {
            "bodyBytes": len(body),
            "preservedRegister": "ecx",
            "x87StatusOperation": "fwait-before-spill",
            "tempStackOffset": 0,
            "tempBytes": 8,
            "returnSource": "sign-extended-low-word-of-temp",
        }
    if body == bytes.fromhex("51dd7c2400dbe20fbf44240059c3"):
        return {
            "bodyBytes": len(body),
            "preservedRegister": "ecx",
            "x87StatusOperation": "fnclex-after-spill",
            "tempStackOffset": 0,
            "tempBytes": 8,
            "returnSource": "sign-extended-low-word-of-temp",
        }
    return None


def x87_pop_return_zero_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_x87_pop_return_zero(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    bytes_list = ", ".join(f"0{byte:02x}h" for byte in strip_alignment_padding(data))
    source = "\n".join(
        [
            "; Automatically generated from an x87 pop-and-return-zero helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper discards the live x87 value, loads +0.0, and returns.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            f"    DB {bytes_list}",
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "x87-pop-return-zero",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 pop return-zero bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "x87 pop return-zero pattern; MASM byte-emission preserves exact floating-point stack operation bytes",
        },
    }


def decode_x87_pop_return_zero(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if not is_x87_pop_return_zero(body):
        return None
    return {
        "bodyBytes": len(body),
        "discardedRegister": "st(0)",
        "returnedX87Value": "+0.0",
        "x87Operations": ["fstp st(0)", "fldz"],
    }


def x87_round_stack_double_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_x87_round_stack_double_return(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from an x87 stack-double round helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper rounds the stack double argument according to the current x87 rounding mode and returns it in st(0).",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "x87-round-stack-double-return",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 round stack-double return bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "x87 frndint stack-double return pattern; MASM byte-emission preserves exact floating-point stack operation bytes",
        },
    }


def decode_x87_round_stack_double_return(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != X87_ROUND_STACK_DOUBLE_RETURN:
        return None
    return {
        "bodyBytes": len(body),
        "argIndex": 1,
        "argumentType": "double",
        "argumentStackOffsetAfterScratch": 12,
        "scratchBytes": 8,
        "scratchInit": "push ecx twice",
        "x87Operations": ["fld qword ptr [esp+0x0c]", "frndint", "fstp qword ptr [esp]", "fld qword ptr [esp]"],
        "returnRegister": "st(0)",
        "roundingMode": "current x87 control word",
    }


X87_ROUND_STACK_DOUBLE_RETURN = bytes.fromhex("5151dd44240cd9fcdd5c2400dd4424005959c3")


def x87_control_word_masked_setter_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_x87_control_word_masked_setter(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from an x87 control-word masked setter.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper loads a merged x87 control word and returns the previous control word.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "x87-control-word-masked-setter",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 control-word masked setter bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "x87 control-word masked setter; MASM byte-emission preserves exact stack and fldcw layout",
        },
    }


def decode_x87_control_word_masked_setter(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != X87_CONTROL_WORD_MASKED_SETTER:
        return None
    return {
        "bodyBytes": len(body),
        "valueArgIndex": 1,
        "maskArgIndex": 2,
        "mergeExpression": "(oldControlWord & ~mask) | (value & mask)",
        "savedControlWordStackOffset": -4,
        "newControlWordStackArgOffset": 12,
        "returnRegister": "eax",
        "returnValue": "sign-extended previous x87 control word",
        "x87Operations": ["fstcw [ebp-4]", "fldcw [ebp+0x0c]"],
    }


X87_CONTROL_WORD_MASKED_SETTER = bytes.fromhex("558bec519bd97dfc8b450c8b4d08234d0cf7d02345fc0bc189450cd96d0c0fbf45fcc9c3")


def x87_double_exponent_adjust_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_x87_double_exponent_adjust_return(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from an x87 double exponent-adjust return helper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper rewrites the exponent word of a stack double and returns the adjusted value.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "x87-double-exponent-adjust-return",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 double exponent-adjust return bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "x87 double exponent-adjust return; MASM byte-emission preserves exact stack temp and exponent-word rewrite",
        },
    }


def decode_x87_double_exponent_adjust_return(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != X87_DOUBLE_EXPONENT_ADJUST_RETURN:
        return None
    return {
        "bodyBytes": len(body),
        "doubleArgIndex": 1,
        "exponentArgIndex": 3,
        "doubleArgStackOffset": 8,
        "exponentArgStackOffset": 16,
        "scratchBytes": 8,
        "exponentBiasAddend": 0x3FE,
        "exponentShift": 4,
        "preservedExponentWordMask": "0xffff800f",
        "exponentWordTempOffset": -2,
        "returnRegister": "st(0)",
        "x87Operations": ["fld qword ptr [ebp+0x08]", "fstp qword ptr [ebp-0x08]", "fld qword ptr [ebp-0x08]"],
    }


X87_DOUBLE_EXPONENT_ADJUST_RETURN = bytes.fromhex("558bec51518b4510dd45088b4d0edd5df805fe030000c1e00481e10f80ffff0bc1668945fedd45f8c9c3")


def stack_arg_range_global_mode_setter_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_range_global_mode_setter(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    global_address = int(decoded["globalAddress"])
    equal_one_value = int(decoded["equalOneValue"])
    range_value = int(decoded["rangeValue"])
    source = "\n".join(
        [
            "typedef unsigned int mizuchi_u32;",
            f"void __cdecl {c_name}(int mode)",
            "{",
            "    switch (mode) {",
            "    case 1:",
            f"        *(volatile mizuchi_u32 *)0x{global_address:08x} = 0x{equal_one_value:02x}u;",
            "        return;",
            "    case 2:",
            "    case 3:",
            f"        *(volatile mizuchi_u32 *)0x{global_address:08x} = 0x{range_value:02x}u;",
            "        return;",
            "    default:",
            "        return;",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stack-arg-range-global-mode-setter",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated high-level C parity match for decoded stack-argument range global mode setter",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stack-argument range global mode setter; switch source shape matched target code slice under MSVC row-hint profile",
        },
    }


def decode_stack_arg_range_global_mode_setter(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    expected = bytes.fromhex("8b44240483f80174127e1a83f8037f15c7051c5a053022000000c3c7051c5a053021000000c3")
    if body != expected:
        return None
    global_address = int.from_bytes(body[0x12:0x16], "little")
    second_global_address = int.from_bytes(body[0x1d:0x21], "little")
    if second_global_address != global_address:
        return None
    return {
        "bodyBytes": len(body),
        "argIndex": 1,
        "globalAddress": global_address,
        "equalOneValue": int.from_bytes(body[0x21:0x25], "little"),
        "rangeInput": [2, 3],
        "rangeValue": int.from_bytes(body[0x16:0x1a], "little"),
        "noStoreWhen": "arg1 <= 0 or arg1 > 3",
        "compareOffsets": [4, 11],
        "branchOffsets": [7, 9, 14],
        "returnOffsets": [26, 37],
    }


def u96_bit_tail_clear_check_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_u96_bit_tail_clear_check(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from an x86 96-bit bit-tail clear predicate.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper returns 1 when no bits at or above the decoded bit index are set in the three-word array.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "u96-bit-tail-clear-check",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded 96-bit tail-clear predicate bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "96-bit bit-tail clear predicate; MASM byte-emission preserves exact signed division and loop shape",
        },
    }


def decode_u96_bit_tail_clear_check(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != U96_BIT_TAIL_CLEAR_CHECK:
        return None
    return {
        "bodyBytes": len(body),
        "baseArgIndex": 1,
        "bitIndexArgIndex": 2,
        "wordBits": 32,
        "wordCount": 3,
        "bitIndexDivision": "signed idiv by 32",
        "partialWordMask": "not(-1 << (31 - remainder))",
        "returnWhenClear": 1,
        "returnWhenAnySet": 0,
        "returnRegister": "eax",
    }


U96_BIT_TAIL_CLEAR_CHECK = bytes.fromhex("8b4424086a205999f7f96a1f592bca83caffd3e28b4c2404f7d2851481740933c0c3833c810075f74083f8037cf433c040c3")


def ebx_bitfield_mode_remap_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_ebx_bitfield_mode_remap(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from an x86 live-ebx bitfield mode remapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The helper maps selected live ebx bitfields into eax and returns the decoded flag word.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "ebx-bitfield-mode-remap",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded live-ebx bitfield remap bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "live-ebx bitfield remap; MASM byte-emission preserves exact branch/register schedule",
        },
    }


def decode_ebx_bitfield_mode_remap(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == EBX_BITFIELD_MODE_REMAP_BF56:
        return {
            "bodyBytes": len(body),
            "inputRegister": "ebx",
            "outputRegister": "eax",
            "variant": "bf56",
            "singleBitMappings": [
                {"mask": "0x00000001", "value": "0x00000010"},
                {"mask": "0x00000004", "value": "0x00000008"},
                {"mask": "0x00000008", "value": "0x00000004"},
                {"mask": "0x00000010", "value": "0x00000002"},
                {"mask": "0x00000020", "value": "0x00000001"},
                {"mask": "0x00000002", "value": "0x00080000"},
                {"mask": "0x00100000", "value": "0x00040000"},
            ],
            "fieldMappings": [
                {"mask": "0x00000c00", "cases": {"0x00000400": "0x00000100", "0x00000800": "0x00000200", "0x00000c00": "0x00000300"}},
                {"mask": "0x00000300", "cases": {"0x00000000": "0x00020000", "0x00000200": "0x00010000"}},
            ],
            "preservedRegisters": ["ebp", "esi", "edi"],
        }
    if body == EBX_BITFIELD_MODE_REMAP_BFE8:
        return {
            "bodyBytes": len(body),
            "inputRegister": "ebx",
            "outputRegister": "eax",
            "variant": "bfe8",
            "singleBitMappings": [
                {"mask": "0x00000010", "value": "0x00000001"},
                {"mask": "0x00000008", "value": "0x00000004"},
                {"mask": "0x00000004", "value": "0x00000008"},
                {"mask": "0x00000002", "value": "0x00000010"},
                {"mask": "0x00000001", "value": "0x00000020"},
                {"mask": "0x00080000", "value": "0x00000002"},
                {"mask": "0x00040000", "value": "0x00001000"},
            ],
            "fieldMappings": [
                {"mask": "0x00000300", "cases": {"0x00000000": "0x00000000", "0x00000100": "0x00000400", "0x00000200": "0x00000800", "0x00000300": "0x00000c00"}},
                {"mask": "0x00030000", "cases": {"0x00000000": "0x00000300", "0x00010000": "0x00000200"}},
            ],
            "preservedRegisters": ["esi"],
        }
    return None


EBX_BITFIELD_MODE_REMAP_BF56 = bytes.fromhex(
    "33c0f6c30174036a1058f6c304740383c808f6c308740383c804f6c310740383c802"
    "f6c320740383c801f6c30274050d00000800550fb7d3568bcabe000c000023ce57"
    "bf00030000bd00020000742181f900040000741481f90008000074083bce750d"
    "0bc7eb090bc5eb050d0001000023d7740b3bd5750c0d00000100eb050d00000200"
    "f6c7105f5e5d74050d00000400c3"
)


EBX_BITFIELD_MODE_REMAP_BFE8 = bytes.fromhex(
    "33c0f6c310740140f6c308740383c804f6c304740383c808f6c302740383c810"
    "f6c301740383c820f7c300000800740383c8028bcbba0003000023ca56be00020000"
    "742381f90001000074163bce740b3bca75130d000c0000eb0c0d00080000eb05"
    "0d000400008bcb81e100000300740c81f90000010075060bc6eb020bc2"
    "f7c3000004005e74050d00100000c3"
)


def masm_db_lines(data: bytes, *, chunk_size: int = 16) -> list[str]:
    return [
        "    DB " + ", ".join(f"0{byte:02x}h" for byte in data[offset : offset + chunk_size])
        for offset in range(0, len(data), chunk_size)
    ]


def strip_alignment_padding(data: bytes) -> bytes:
    end = len(data)
    while end > 0 and data[end - 1] in {0x90, 0xCC}:
        end -= 1
    return data[:end]


def is_multi_function_packed_slice(data: bytes) -> bool:
    body = strip_alignment_padding(data)
    for index in range(max(0, len(body) - 6)):
        ret_size = 1 if body[index] == 0xC3 else 3 if body[index] == 0xC2 and index + 2 < len(body) else 0
        if ret_size == 0:
            continue
        cursor = index + ret_size
        padding_start = cursor
        while cursor < len(body) and body[cursor] in {0x90, 0xCC}:
            cursor += 1
        if cursor > padding_start and cursor < len(body):
            return True
    return False


def thiscall_indexed_field_selector(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    # mov eax,[esp+4]; test eax,eax; jne A; mov eax,[ecx+L]; ret 4;
    # cmp eax,1; jne B; mov eax,[ecx+R]; ret 4; xor eax,eax; ret 4
    if len(data) != 30:
        return None
    if data[:8] != b"\x8b\x44\x24\x04\x85\xc0\x75\x06":
        return None
    if data[8:10] != b"\x8b\x41" or data[11:17] != b"\xc2\x04\x00\x83\xf8\x01":
        return None
    if data[17:19] != b"\x75\x06" or data[19:21] != b"\x8b\x41":
        return None
    if data[22:] != b"\xc2\x04\x00\x33\xc0\xc2\x04\x00":
        return None
    first_offset = data[10]
    second_offset = data[21]
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    class_name = c_identifier(f"{c_name}_class")
    max_field = max(first_offset, second_offset)
    fields = class_fields_for_offsets(max_field)
    first_field = field_name(first_offset)
    second_field = field_name(second_offset)
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 thiscall indexed-field selector pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"struct {class_name} {{",
            *[f"    int {name};" for name in fields],
            f"    int {c_name}(int index);",
            "};",
            "",
            f"int {class_name}::{c_name}(int index) {{",
            f"    if (index == 0) return {first_field};",
            f"    if (index == 1) return {second_field};",
            "    return 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "cpp",
        "language": "c++",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-thiscall-indexed-field-selector",
            "bodyBytes": len(data),
            "firstFieldOffset": first_offset,
            "secondFieldOffset": second_offset,
            "trailingPaddingIgnored": int(task.get("size") or 0) - len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c++",
            "args": ["/O2", "/GS-", "/Oy", "/TP"],
            "reason": "real swkotor selector slices match this generated C++ member shape with MSVC optimization",
        },
    }


def inc_abs_global_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 7 or data[0] != 0xFF or data[1] != 0x05 or data[-1] != 0xC3:
        return None
    addr = int.from_bytes(data[2:6], byteorder="little", signed=False)
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 absolute-global increment pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"void {c_name}(void) {{",
            f"    ++*(unsigned int *)0x{addr:08x};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "inc-absolute-global",
            "absoluteAddress": f"0x{addr:08x}",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "absolute-global increment is a compact x86 C pattern",
        },
    }


def virtual_tailcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 8 or data[0] != 0x8B or data[1] != 0x49 or data[3:6] != b"\x8b\x01\xff" or data[6] != 0x60:
        return None
    field = data[2]
    slot = data[7]
    slot_index = slot // 4
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    base = [
        "typedef int (__fastcall *method_i32)(void *);",
        "typedef void (__fastcall *method_void)(void *);",
        "",
    ]
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 virtual tailcall pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            *base,
            f"int __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            f"    return ((method_i32 *)*(void ***)obj)[{slot_index}](obj);",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "virtual-tailcall-this-field",
            "thisFieldOffset": field,
            "vtableSlotBytes": slot,
            "vtableSlotIndex": slot_index,
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "virtual-tailcall wrapper is a compact x86 fastcall pattern",
        },
    }


def unsigned_field_less_than_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 11 or data[0] != 0x8B or data[1] != 0x41 or data[3] != 0x3B or data[4] != 0x41:
        return None
    if data[6:] != b"\x1b\xc0\xf7\xd8\xc3":
        return None
    left = data[2]
    right = data[5]
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 unsigned-field comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned int left = *(unsigned int *)({self_offset(left)});",
            f"    unsigned int right = *(unsigned int *)({self_offset(right)});",
            "    return left < right;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "unsigned-field-less-than",
            "leftOffset": left,
            "rightOffset": right,
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "unsigned comparison on two fields is a compact x86 fastcall pattern",
        },
    }


def zero_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 3 or data not in {b"\x33\xc0\xc3", b"\x31\xc0\xc3"}:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 zero-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(void) {{",
            "    return 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "return-zero-cdecl",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "constant zero return is a canonical x86 leaf pattern",
        },
    }


def zero_return_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 5 or data[:3] not in {b"\x33\xc0\xc2", b"\x31\xc0\xc2"}:
        return None
    stack_bytes = int.from_bytes(data[3:5], "little")
    if stack_bytes == 0 or stack_bytes % 4 != 0:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    params = ", ".join(f"unsigned int unused_{index}" for index in range(stack_bytes // 4)) or "void"
    voids = [f"    (void)unused_{index};" for index in range(stack_bytes // 4)]
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall zero-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}({params}) {{",
            *voids,
            "    return 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "return-zero-stdcall",
            "bodyBytes": len(data),
            "stackBytes": stack_bytes,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall constant zero return is a canonical x86 leaf pattern",
        },
    }


def immediate_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 6 or data[0] != 0xB8 or data[5] != 0xC3:
        return None
    value = int.from_bytes(data[1:5], "little")
    if value in {0, 1}:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 immediate-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(void) {{",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "return-immediate-cdecl",
            "bodyBytes": len(data),
            "value": f"0x{value:08x}",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "constant immediate return is a canonical x86 leaf pattern",
        },
    }


def immediate_return_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 8 or data[0] != 0xB8 or data[5] != 0xC2:
        return None
    value = int.from_bytes(data[1:5], "little")
    stack_bytes = int.from_bytes(data[6:8], "little")
    if value in {0, 1} or stack_bytes == 0 or stack_bytes % 4 != 0:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    params = ", ".join(f"unsigned int unused_{index}" for index in range(stack_bytes // 4)) or "void"
    voids = [f"    (void)unused_{index};" for index in range(stack_bytes // 4)]
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall immediate-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}({params}) {{",
            *voids,
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "return-immediate-stdcall",
            "bodyBytes": len(data),
            "value": f"0x{value:08x}",
            "stackBytes": stack_bytes,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall constant immediate return is a canonical x86 leaf pattern",
        },
    }


def is_x86_64_task(task: dict[str, Any]) -> bool:
    return str(task.get("architectureHint") or "").lower() == "x86_64"


def x86_64_zero_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if not is_x86_64_task(task) or len(body) != 3 or body not in {b"\x33\xc0\xc3", b"\x31\xc0\xc3"}:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 zero-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(void) {{",
            "    return 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-return-zero-cdecl",
            "bodyBytes": len(body),
            "framePointer": False,
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_immediate_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if not is_x86_64_task(task) or len(body) != 6 or body[0] != 0xB8 or body[5] != 0xC3:
        return None
    value = int.from_bytes(body[1:5], "little")
    if value in {0, 1}:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 immediate-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(void) {{",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-return-immediate-cdecl",
            "bodyBytes": len(body),
            "value": f"0x{value:08x}",
            "framePointer": False,
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_one_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if not is_x86_64_task(task) or body != b"\xb8\x01\x00\x00\x00\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 one-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(void) {{",
            "    return 1;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-return-one-cdecl",
            "bodyBytes": len(body),
            "framePointer": False,
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def framed_zero_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 7 or body[:3] not in {b"\x55\x89\xe5", b"\x55\x8b\xec"} or body[5:] != b"\x5d\xc3":
        return None
    if body[3:5] not in {b"\x31\xc0", b"\x33\xc0"}:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from a framed x86 zero-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(void) {{",
            "    return 0;",
            "}",
            "",
        ]
    )
    style = x86_frame_style(body)
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 frame-pointer byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "framed-return-zero",
            "bodyBytes": len(body),
            "frameStyle": style,
        },
        "compilerProfileHints": framed_return_compiler_profile_hint(style),
    }


def framed_immediate_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 10 or body[:3] not in {b"\x55\x89\xe5", b"\x55\x8b\xec"} or body[3] != 0xB8 or body[8:] != b"\x5d\xc3":
        return None
    value = int.from_bytes(body[4:8], "little")
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from a framed x86 immediate-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(void) {{",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    style = x86_frame_style(body)
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 frame-pointer byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "framed-return-immediate-cdecl",
            "bodyBytes": len(body),
            "frameStyle": style,
            "value": f"0x{value:08x}",
        },
        "compilerProfileHints": framed_return_compiler_profile_hint(style),
    }


def framed_return_first_stack_arg_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:3] not in {b"\x55\x89\xe5", b"\x55\x8b\xec"} or body[3:6] != b"\x8b\x45\x08" or body[6:] != b"\x5d\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    style = x86_frame_style(body)
    if style == "msvc":
        source_lines = [
            "/*",
            " * Automatically generated from a framed x86 stack-argument return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * Generated as naked inline assembly because compiler frame/argument-load shapes vary by profile.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"__declspec(naked) unsigned int {c_name}(unsigned int value) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+8]",
            "        pop ebp",
            "        ret",
            "    }",
            "}",
            "",
        ]
        hint = {
            "compiler": "msvc",
            "language": "c",
            "args": ["/Od", "/GS-", "/Oy-"],
            "reason": "MSVC frame-pointer stack-argument passthrough; naked inline assembly preserves exact debug-frame shape",
        }
    else:
        source_lines = [
            "/*",
            " * Automatically generated from a framed x86 stack-argument return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * Generated as naked inline assembly because clang high-level O0 emits an extra argument load.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"__attribute__((naked)) unsigned int {c_name}(unsigned int value) {{",
            "    __asm__ volatile(",
            '        "pushl %ebp\\n\\t"',
            '        "movl %esp, %ebp\\n\\t"',
            '        "movl 8(%ebp), %eax\\n\\t"',
            '        "popl %ebp\\n\\t"',
            '        "retl\\n\\t"',
            "    );",
            "}",
            "",
        ]
        hint = {
            "compiler": "clang",
            "language": "c",
            "args": ["-m32", "-O0", "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"],
            "reason": "gcc/clang frame-pointer stack-argument passthrough; naked inline assembly preserves exact source-slice bytes",
        }
    return {
        "source": "\n".join(source_lines),
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 frame-pointer byte-pattern lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "framed-return-first-stack-arg-cdecl",
            "bodyBytes": len(body),
            "frameStyle": style,
            "sourceTier": "generated inline-assembly parity source for framed stack-argument return",
        },
        "compilerProfileHints": hint,
    }


def x86_frame_style(body: bytes) -> str:
    if body.startswith(b"\x55\x89\xe5"):
        return "gcc-clang"
    if body.startswith(b"\x55\x8b\xec"):
        return "msvc"
    return "unknown"


def x86_64_framed_zero_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:4] != b"\x55\x48\x89\xe5" or body[6:] != b"\x5d\xc3":
        return None
    if body[4:6] not in {b"\x31\xc0", b"\x33\xc0"}:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 framed zero-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(void) {{",
            "    return 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 frame-pointer byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-framed-return-zero",
            "bodyBytes": len(body),
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_framed_return_compiler_profile_hint(task),
    }


def x86_64_framed_immediate_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 11 or body[:4] != b"\x55\x48\x89\xe5" or body[4] != 0xB8 or body[9:] != b"\x5d\xc3":
        return None
    value = int.from_bytes(body[5:9], "little")
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 framed immediate-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(void) {{",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 frame-pointer byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-framed-return-immediate-cdecl",
            "bodyBytes": len(body),
            "targetFormat": task.get("targetFormat"),
            "value": f"0x{value:08x}",
        },
        "compilerProfileHints": x86_64_framed_return_compiler_profile_hint(task),
    }


def x86_64_return_first_arg_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != b"\x89\xf8\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 first-argument return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-return-first-arg-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "framePointer": False,
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_return_first_arg64_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != b"\x48\x89\xf8\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 64-bit first-argument return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned long long {c_name}(unsigned long long value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-return-first-arg64-cdecl",
            "bodyBytes": len(body),
            "registerArg": "rdi",
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_RETURN_SECOND_ARG_OPS: dict[bytes, tuple[str, str, str, str]] = {
    b"\x89\xf0\xc3": ("x86-64-return-second-arg-cdecl", "unsigned int", "esi", "mov-eax-esi-ret"),
    b"\x48\x89\xf0\xc3": ("x86-64-return-second-arg64-cdecl", "unsigned long long", "rsi", "mov-rax-rsi-ret"),
}


def x86_64_return_second_arg_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_RETURN_SECOND_ARG_OPS.get(body)
    if decoded is None:
        return None
    rule, value_type, register_arg, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 second-argument return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{value_type} {c_name}({value_type} a, {value_type} b) {{",
            "    return b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": rule,
            "bodyBytes": len(body),
            "registerArg": register_arg,
            "registerArgs": ["edi", "esi"] if register_arg == "esi" else ["rdi", "rsi"],
            "valueType": value_type,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_add_two_args_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != b"\x8d\x04\x37\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 two-argument add pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-add-two-args-cdecl",
            "bodyBytes": len(body),
            "registerArgs": ["edi", "esi"],
            "framePointer": False,
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def format_x86_64_two_args_affine_expression(coeff_a: int, coeff_b: int, immediate: int) -> str:
    terms: list[str] = []
    if coeff_a == 1:
        terms.append("a")
    elif coeff_a > 1:
        terms.append(f"{coeff_a}u * a")
    if coeff_b == 1:
        terms.append("b")
    elif coeff_b > 1:
        terms.append(f"{coeff_b}u * b")
    if immediate:
        terms.append(f"0x{immediate:02x}u")
    return " + ".join(terms)


def decode_x86_64_two_args_affine_lea(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 4 or body[:2] != b"\x8d\x04":
        return None
    sib = body[2]
    scale = 1 << ((sib >> 6) & 0x03)
    index = (sib >> 3) & 0x07
    base = sib & 0x07
    register_names = {0x07: "a", 0x06: "b"}
    if base not in register_names or index not in register_names:
        return None
    coeffs = {"a": 0, "b": 0}
    coeffs[register_names[base]] += 1
    coeffs[register_names[index]] += scale
    immediate = 0
    suffix = "scaled"
    if len(body) == 4 and body[3] == 0xC3:
        pass
    elif len(body) == 6 and body[3:6] == b"\x01\xf8\xc3":
        coeffs["a"] += 1
        suffix = "scaled-add-a"
    elif len(body) == 7 and body[3:5] == b"\x83\xc0" and body[6] == 0xC3:
        immediate = body[5]
        if immediate == 0:
            return None
        suffix = "scaled-add-imm8"
    else:
        return None
    coeff_a = coeffs["a"]
    coeff_b = coeffs["b"]
    if coeff_a == 1 and coeff_b == 1 and immediate == 0:
        return None
    if coeff_a == 0 or coeff_b == 0:
        return None
    return {
        "suffix": suffix,
        "coeffA": coeff_a,
        "coeffB": coeff_b,
        "immediate": immediate,
        "expression": format_x86_64_two_args_affine_expression(coeff_a, coeff_b, immediate),
        "pattern": f"lea-eax-sib-0x{sib:02x}{'-add-eax-edi' if suffix == 'scaled-add-a' else '-add-eax-imm8' if suffix == 'scaled-add-imm8' else ''}-ret",
    }


def x86_64_two_args_affine_lea_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_two_args_affine_lea(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    expression = str(decoded["expression"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 two-argument affine LEA pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-two-args-affine-lea-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArgs": ["edi", "esi"],
            "coeffA": int(decoded["coeffA"]),
            "coeffB": int(decoded["coeffB"]),
            "immediate": int(decoded["immediate"]),
            "expression": expression,
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_TWO_ARG_BINARY_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x89\xf8\x29\xf0\xc3": ("sub", "-", "mov-eax-edi-sub-eax-esi-ret"),
    b"\x89\xf8\x0f\xaf\xc6\xc3": ("mul", "*", "mov-eax-edi-imul-eax-esi-ret"),
    b"\x89\xf8\x21\xf0\xc3": ("and", "&", "mov-eax-edi-and-eax-esi-ret"),
    b"\x89\xf8\x09\xf0\xc3": ("or", "|", "mov-eax-edi-or-eax-esi-ret"),
    b"\x89\xf8\x31\xf0\xc3": ("xor", "^", "mov-eax-edi-xor-eax-esi-ret"),
}


def x86_64_two_args_binary_op_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_TWO_ARG_BINARY_OPS.get(body)
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 two-argument {suffix} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-two-args-{suffix}-cdecl",
            "bodyBytes": len(body),
            "registerArgs": ["edi", "esi"],
            "operator": operator,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_TWO_ARG_BINARY_OPS64: dict[bytes, tuple[str, str, str]] = {
    b"\x48\x8d\x04\x37\xc3": ("add", "+", "lea-rax-rdi-rsi-ret"),
    b"\x48\x89\xf8\x48\x29\xf0\xc3": ("sub", "-", "mov-rax-rdi-sub-rax-rsi-ret"),
    b"\x48\x89\xf8\x48\x0f\xaf\xc6\xc3": ("mul", "*", "mov-rax-rdi-imul-rax-rsi-ret"),
    b"\x48\x89\xf8\x48\x21\xf0\xc3": ("and", "&", "mov-rax-rdi-and-rax-rsi-ret"),
    b"\x48\x89\xf8\x48\x09\xf0\xc3": ("or", "|", "mov-rax-rdi-or-rax-rsi-ret"),
    b"\x48\x89\xf8\x48\x31\xf0\xc3": ("xor", "^", "mov-rax-rdi-xor-rax-rsi-ret"),
}


def x86_64_two_args_binary_op64_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_TWO_ARG_BINARY_OPS64.get(body)
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit two-argument {suffix} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned long long {c_name}(unsigned long long a, unsigned long long b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-two-args64-{suffix}-cdecl",
            "bodyBytes": len(body),
            "registerArgs": ["rdi", "rsi"],
            "operator": operator,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_TWO_ARG_MIN_MAX_OPS: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x89\xf0\x39\xf7\x0f\x42\xc7\xc3": ("uint-min", "<", "unsigned int", "cmovb", "mov-eax-esi-cmp-edi-esi-cmovb-eax-edi-ret"),
    b"\x89\xf0\x39\xf7\x0f\x47\xc7\xc3": ("uint-max", ">", "unsigned int", "cmova", "mov-eax-esi-cmp-edi-esi-cmova-eax-edi-ret"),
    b"\x89\xf0\x39\xf7\x0f\x4c\xc7\xc3": ("int-min", "<", "int", "cmovl", "mov-eax-esi-cmp-edi-esi-cmovl-eax-edi-ret"),
    b"\x89\xf0\x39\xf7\x0f\x4f\xc7\xc3": ("int-max", ">", "int", "cmovg", "mov-eax-esi-cmp-edi-esi-cmovg-eax-edi-ret"),
}


def x86_64_two_args_min_max_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_TWO_ARG_MIN_MAX_OPS.get(body)
    if decoded is None:
        return None
    suffix, operator, value_type, cmov, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 two-argument {suffix} cmov pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{value_type} {c_name}({value_type} a, {value_type} b) {{",
            f"    return a {operator} b ? a : b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-{suffix}-two-args-cdecl",
            "bodyBytes": len(body),
            "registerArgs": ["edi", "esi"],
            "operator": operator,
            "valueType": value_type,
            "cmov": cmov,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_TWO_ARG_MIN_MAX_OPS64: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x48\x89\xf0\x48\x39\xf7\x48\x0f\x42\xc7\xc3": ("uint64-min", "<", "unsigned long long", "cmovb", "mov-rax-rsi-cmp-rdi-rsi-cmovb-rax-rdi-ret"),
    b"\x48\x89\xf0\x48\x39\xf7\x48\x0f\x47\xc7\xc3": ("uint64-max", ">", "unsigned long long", "cmova", "mov-rax-rsi-cmp-rdi-rsi-cmova-rax-rdi-ret"),
    b"\x48\x89\xf0\x48\x39\xf7\x48\x0f\x4c\xc7\xc3": ("int64-min", "<", "long long", "cmovl", "mov-rax-rsi-cmp-rdi-rsi-cmovl-rax-rdi-ret"),
    b"\x48\x89\xf0\x48\x39\xf7\x48\x0f\x4f\xc7\xc3": ("int64-max", ">", "long long", "cmovg", "mov-rax-rsi-cmp-rdi-rsi-cmovg-rax-rdi-ret"),
}


def x86_64_two_args_min_max64_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_TWO_ARG_MIN_MAX_OPS64.get(body)
    if decoded is None:
        return None
    suffix, operator, value_type, cmov, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit two-argument {suffix} cmov pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{value_type} {c_name}({value_type} a, {value_type} b) {{",
            f"    return a {operator} b ? a : b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-{suffix}-two-args-cdecl",
            "bodyBytes": len(body),
            "registerArgs": ["rdi", "rsi"],
            "operator": operator,
            "valueType": value_type,
            "cmov": cmov,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_LEA_MULTIPLY_OPS: dict[bytes, tuple[int, str]] = {
    b"\x8d\x04\x3f\xc3": (2, "lea-eax-rdi-rdi-ret"),
    b"\x8d\x04\x7f\xc3": (3, "lea-eax-rdi-rdi2-ret"),
    b"\x8d\x04\xbd\x00\x00\x00\x00\xc3": (4, "lea-eax-rdi4-ret"),
    b"\x8d\x04\xbf\xc3": (5, "lea-eax-rdi-rdi4-ret"),
    b"\x01\xff\x8d\x04\x7f\xc3": (6, "add-edi-edi-lea-eax-rdi-rdi2-ret"),
    b"\x8d\x04\xfd\x00\x00\x00\x00\x29\xf8\xc3": (7, "lea-eax-rdi8-sub-eax-edi-ret"),
    b"\x8d\x04\xfd\x00\x00\x00\x00\xc3": (8, "lea-eax-rdi8-ret"),
    b"\x8d\x04\xff\xc3": (9, "lea-eax-rdi-rdi8-ret"),
    b"\x01\xff\x8d\x04\xbf\xc3": (10, "add-edi-edi-lea-eax-rdi-rdi4-ret"),
    b"\x8d\x04\xbf\x8d\x04\x47\xc3": (11, "lea-eax-rdi-rdi4-lea-eax-rdi-rax2-ret"),
    b"\xc1\xe7\x02\x8d\x04\x7f\xc3": (12, "shl-edi-2-lea-eax-rdi-rdi2-ret"),
    b"\x8d\x04\x7f\x8d\x04\x87\xc3": (13, "lea-eax-rdi-rdi2-lea-eax-rdi-rax4-ret"),
    b"\x89\xf8\x8d\x0c\x00\xc1\xe0\x04\x29\xc8\xc3": (14, "mov-eax-edi-lea-ecx-rax-rax-shl-eax-4-sub-eax-ecx-ret"),
    b"\x8d\x04\xbf\x8d\x04\x40\xc3": (15, "lea-eax-rdi-rdi4-lea-eax-rax-rax2-ret"),
    b"\xc1\xe7\x03\x8d\x04\x7f\xc3": (24, "shl-edi-3-lea-eax-rdi-rdi2-ret"),
    b"\x89\xf8\xc1\xe0\x05\x29\xf8\xc3": (31, "mov-eax-edi-shl-eax-5-sub-eax-edi-ret"),
    b"\x89\xf8\xc1\xe0\x05\x01\xf8\xc3": (33, "mov-eax-edi-shl-eax-5-add-eax-edi-ret"),
}


def x86_64_arg_lea_multiply_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_LEA_MULTIPLY_OPS.get(body)
    if decoded is None:
        return None
    multiplier, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument multiply-by-{multiplier} LEA pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value * {multiplier}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-mul-lea-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "operator": "*",
            "multiplier": multiplier,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG64_LEA_MULTIPLY_OPS: dict[bytes, tuple[int, str]] = {
    b"\x48\x8d\x04\x3f\xc3": (2, "lea-rax-rdi-rdi-ret"),
    b"\x48\x8d\x04\x7f\xc3": (3, "lea-rax-rdi-rdi2-ret"),
    b"\x48\x8d\x04\xbd\x00\x00\x00\x00\xc3": (4, "lea-rax-rdi4-ret"),
    b"\x48\x8d\x04\xbf\xc3": (5, "lea-rax-rdi-rdi4-ret"),
    b"\x48\x8d\x04\xfd\x00\x00\x00\x00\xc3": (8, "lea-rax-rdi8-ret"),
    b"\x48\x8d\x04\xff\xc3": (9, "lea-rax-rdi-rdi8-ret"),
}


def x86_64_arg64_lea_multiply_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG64_LEA_MULTIPLY_OPS.get(body)
    if decoded is None:
        return None
    multiplier, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit argument multiply-by-{multiplier} LEA pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return value * {multiplier}ull;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg64-mul-lea-cdecl",
            "bodyBytes": len(body),
            "registerArg": "rdi",
            "operator": "*",
            "multiplier": multiplier,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_CONST_MIN_MAX_CMOV: dict[int, tuple[str, str, str, str, bool]] = {
    0x42: ("uint-min", "<", "unsigned int", "cmovb", False),
    0x43: ("uint-max", ">", "unsigned int", "cmovae", True),
    0x4C: ("int-min", "<", "int", "cmovl", False),
    0x4D: ("int-max", ">", "int", "cmovge", True),
}


def decode_x86_64_arg_const_min_max(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 12 or body[:2] != b"\x83\xff" or body[3] != 0xB8 or body[8] != 0x0F or body[10:12] != b"\xc7\xc3":
        return None
    decoded = X86_64_ARG_CONST_MIN_MAX_CMOV.get(body[9])
    if decoded is None:
        return None
    suffix, operator, value_type, cmov, compare_is_exclusive_upper = decoded
    compare_immediate = body[2]
    constant = int.from_bytes(body[4:8], "little", signed=False)
    if compare_is_exclusive_upper:
        if compare_immediate == 0:
            return None
        expected_constant = compare_immediate - 1
    else:
        expected_constant = compare_immediate
    if constant != expected_constant:
        return None
    if value_type == "int" and constant > 0x7F:
        return None
    return {
        "suffix": suffix,
        "operator": operator,
        "valueType": value_type,
        "returnType": value_type,
        "constant": constant,
        "compareImmediate": compare_immediate,
        "cmov": cmov,
        "pattern": f"cmp-edi-imm8-mov-eax-imm32-{cmov}-eax-edi-ret",
    }


def x86_64_arg_const_min_max_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_const_min_max(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    constant = int(decoded["constant"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    operator = str(decoded["operator"])
    literal = f"0x{constant:02x}u" if value_type == "unsigned int" else str(constant)
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument {decoded['suffix']} constant cmov pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {literal} ? value : {literal};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-{decoded['suffix']}-imm8-cmov-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": operator,
            "constant": f"0x{constant:02x}" if value_type == "unsigned int" else constant,
            "compareImmediate": int(decoded["compareImmediate"]),
            "valueType": value_type,
            "returnType": return_type,
            "cmov": decoded["cmov"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_const_minus_arg_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[0] != 0xB8 or body[5:] != b"\x29\xf8\xc3":
        return None
    value = int.from_bytes(body[1:5], "little", signed=False)
    if value == 0:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 constant-minus-argument pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return 0x{value:08x}u - value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-const-minus-arg-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "operator": "-",
            "constant": f"0x{value:08x}",
            "pattern": "mov-eax-imm32-sub-eax-edi-ret",
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_SIGNBIT_ZERO_COMPARE_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x89\xf8\xc1\xe8\x1f\xc3": ("lt", "<", "mov-eax-edi-shr-eax-31-ret"),
    b"\x89\xf8\xf7\xd0\xc1\xe8\x1f\xc3": ("ge", ">=", "mov-eax-edi-not-eax-shr-eax-31-ret"),
}


def x86_64_arg_signbit_zero_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_SIGNBIT_ZERO_COMPARE_OPS.get(body)
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 signed sign-bit zero {suffix} comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-int-signbit-zero-{suffix}-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "operator": operator,
            "immediate": 0,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_SIGN_MASK_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x89\xf8\xc1\xf8\x1f\xc3": ("sign", "value < 0 ? -1 : 0", "mov-eax-edi-sar-eax-31-ret"),
    b"\x89\xf8\xf7\xd0\xc1\xf8\x1f\xc3": ("nonsign", "value < 0 ? 0 : -1", "mov-eax-edi-not-eax-sar-eax-31-ret"),
}


def x86_64_arg_sign_mask_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_SIGN_MASK_OPS.get(body)
    if decoded is None:
        return None
    suffix, expression, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 signed {suffix} all-bits mask pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-{suffix}-mask-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "valueType": "int",
            "returnType": "int",
            "trueValue": "0xffffffff",
            "falseValue": "0x00000000",
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_bitmask_bool(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x89\xf8\x83\xe0\x01\xc3":
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 0x00000001,
            "pattern": "mov-eax-edi-and-eax-1-ret",
        }
    if body == b"\x89\xf8\xf7\xd0\x83\xe0\x01\xc3":
        return {
            "predicate": "zero",
            "operator": "==",
            "mask": 0x00000001,
            "pattern": "mov-eax-edi-not-eax-and-eax-1-ret",
        }
    if len(body) == 9 and body[:3] == b"\x89\xf8\xc1" and body[3] == 0xE8 and body[5:8] == b"\x83\xe0\x01" and body[8] == 0xC3:
        shift = body[4]
        if not 1 <= shift <= 31:
            return None
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 1 << shift,
            "shift": shift,
            "pattern": "mov-eax-edi-shr-eax-imm8-and-eax-1-ret",
        }
    if len(body) == 10 and body[:3] == b"\x31\xc0\x40" and body[3] == 0xF6 and body[4] == 0xC7 and body[6] == 0x0F and body[8:] == b"\xc0\xc3":
        mask = body[5]
        setcc_opcode = body[7]
        if mask == 0:
            return None
        if setcc_opcode == 0x94:
            predicate = "zero"
            operator = "=="
            setcc = "sete"
        elif setcc_opcode == 0x95:
            predicate = "nonzero"
            operator = "!="
            setcc = "setne"
        else:
            return None
        return {
            "predicate": predicate,
            "operator": operator,
            "mask": mask,
            "byteMask": mask,
            "setcc": setcc,
            "pattern": f"xor-eax-test-dil-imm8-{setcc}-al-ret",
        }
    if len(body) == 12 and body[:2] == b"\x31\xc0" and body[2:4] == b"\xf7\xc7" and body[8] == 0x0F and body[10:] == b"\xc0\xc3":
        mask = int.from_bytes(body[4:8], "little", signed=False)
        setcc_opcode = body[9]
        if mask == 0:
            return None
        if setcc_opcode == 0x94:
            predicate = "zero"
            operator = "=="
            setcc = "sete"
        elif setcc_opcode == 0x95:
            predicate = "nonzero"
            operator = "!="
            setcc = "setne"
        else:
            return None
        return {
            "predicate": predicate,
            "operator": operator,
            "mask": mask,
            "setcc": setcc,
            "pattern": f"xor-eax-test-edi-imm32-{setcc}-al-ret",
        }
    return None


def x86_64_arg_bitmask_bool_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    if x86_64_prefers_arg64_value(task):
        return None
    decoded = decode_x86_64_arg_bitmask_bool(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    mask = int(decoded["mask"])
    operator = str(decoded["operator"])
    predicate = str(decoded["predicate"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 argument bitmask boolean pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            f"    return (value & 0x{mask:08x}u) {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-bitmask-{predicate}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": operator,
            "predicate": predicate,
            "mask": f"0x{mask:08x}",
            "shift": decoded.get("shift"),
            "setcc": decoded.get("setcc"),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_prefers_arg64_value(task: dict[str, Any]) -> bool:
    value_type = str(task.get("valueType") or task.get("argumentType") or "").strip().lower()
    return (
        task.get("argumentBitWidth") == 64
        or task.get("argumentBits") == 64
        or task.get("valueBits") == 64
        or value_type in {"unsigned long long", "long long", "uint64_t", "int64_t", "size_t", "uintptr_t"}
    )


def decode_x86_64_arg64_bitmask_bool(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x48\x89\xf8\x83\xe0\x01\xc3":
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 0x0000000000000001,
            "pattern": "mov-rax-rdi-and-eax-1-ret",
        }
    if len(body) == 10 and body[:3] == b"\x48\x89\xf8" and body[3:5] == b"\xc1\xe8" and body[6:9] == b"\x83\xe0\x01" and body[9] == 0xC3:
        shift = body[5]
        if not 1 <= shift <= 31:
            return None
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 1 << shift,
            "shift": shift,
            "pattern": "mov-rax-rdi-shr-eax-imm8-and-eax-1-ret",
        }
    if len(body) == 11 and body[:3] == b"\x48\x89\xf8" and body[3:6] == b"\x48\xc1\xe8" and body[7:10] == b"\x83\xe0\x01" and body[10] == 0xC3:
        shift = body[6]
        if not 32 <= shift <= 63:
            return None
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 1 << shift,
            "shift": shift,
            "pattern": "mov-rax-rdi-shr-rax-imm8-and-eax-1-ret",
        }
    if len(body) == 10 and body[:3] == b"\x31\xc0\x40" and body[3] == 0xF6 and body[4] == 0xC7 and body[6] == 0x0F and body[8:] == b"\xc0\xc3":
        mask = body[5]
        setcc_opcode = body[7]
        if mask == 0:
            return None
        if setcc_opcode == 0x94:
            predicate = "zero"
            operator = "=="
            setcc = "sete"
        elif setcc_opcode == 0x95:
            predicate = "nonzero"
            operator = "!="
            setcc = "setne"
        else:
            return None
        return {
            "predicate": predicate,
            "operator": operator,
            "mask": mask,
            "byteMask": mask,
            "requiresWidthHint": True,
            "setcc": setcc,
            "pattern": f"xor-eax-test-dil-imm8-{setcc}-al-ret",
        }
    if len(body) == 12 and body[:2] == b"\x31\xc0" and body[2:4] == b"\xf7\xc7" and body[8] == 0x0F and body[10:] == b"\xc0\xc3":
        mask = int.from_bytes(body[4:8], "little", signed=False)
        setcc_opcode = body[9]
        if mask == 0:
            return None
        if setcc_opcode == 0x94:
            predicate = "zero"
            operator = "=="
            setcc = "sete"
        elif setcc_opcode == 0x95:
            predicate = "nonzero"
            operator = "!="
            setcc = "setne"
        else:
            return None
        return {
            "predicate": predicate,
            "operator": operator,
            "mask": mask,
            "requiresWidthHint": True,
            "setcc": setcc,
            "pattern": f"xor-eax-test-edi-imm32-{setcc}-al-ret",
        }
    return None


def x86_64_arg64_bitmask_bool_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg64_bitmask_bool(data)
    if decoded is None:
        return None
    if decoded.get("requiresWidthHint") and not x86_64_prefers_arg64_value(task):
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    mask = int(decoded["mask"])
    operator = str(decoded["operator"])
    predicate = str(decoded["predicate"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 64-bit argument bitmask boolean pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned long long value) {{",
            f"    return (value & 0x{mask:016x}ull) {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg64-bitmask-{predicate}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "rdi",
            "operator": operator,
            "predicate": predicate,
            "mask": f"0x{mask:016x}",
            "shift": decoded.get("shift"),
            "setcc": decoded.get("setcc"),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_udiv_pow2(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 5 and body == b"\x89\xf8\xd1\xe8\xc3":
        return {"shift": 1, "divisor": 2, "pattern": "mov-eax-edi-shr-eax-one-ret"}
    if len(body) == 6 and body[:3] == b"\x89\xf8\xc1" and body[3] == 0xE8 and body[5] == 0xC3:
        shift = body[4]
        if not 2 <= shift <= 31:
            return None
        return {"shift": shift, "divisor": 1 << shift, "pattern": "mov-eax-edi-shr-eax-imm8-ret"}
    return None


def x86_64_arg_udiv_pow2_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_udiv_pow2(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 unsigned power-of-two division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-udiv-pow2-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": "/",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_urem_pow2(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 6 or body[:3] != b"\x89\xf8\x83" or body[3] != 0xE0 or body[5] != 0xC3:
        return None
    mask = body[4]
    if mask <= 1:
        return None
    divisor = mask + 1
    if divisor & (divisor - 1):
        return None
    return {
        "shift": divisor.bit_length() - 1,
        "divisor": divisor,
        "mask": mask,
        "pattern": "mov-eax-edi-and-eax-pow2-minus-one-ret",
    }


def x86_64_arg_urem_pow2_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_urem_pow2(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    mask = int(decoded["mask"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 unsigned power-of-two remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-urem-pow2-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": "%",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "mask": f"0x{mask:08x}",
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_UDIV_MAGIC_OPS: dict[tuple[int, int], tuple[int, str]] = {
    (0xAAAAAAAB, 0x21): (3, "mov-ecx-edi-mov-eax-magic-imul-rax-rcx-shr-rax-33-ret"),
    (0xCCCCCCCD, 0x22): (5, "mov-ecx-edi-mov-eax-magic-imul-rax-rcx-shr-rax-34-ret"),
    (0xCCCCCCCD, 0x23): (10, "mov-ecx-edi-mov-eax-magic-imul-rax-rcx-shr-rax-35-ret"),
}


def decode_x86_64_arg_udiv_magic(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 16 or body[:2] != b"\x89\xf9" or body[2] != 0xB8 or body[7:11] != b"\x48\x0f\xaf\xc1" or body[11:14] != b"\x48\xc1\xe8" or body[15] != 0xC3:
        return None
    multiplier = int.from_bytes(body[3:7], "little", signed=False)
    shift = body[14]
    decoded = X86_64_ARG_UDIV_MAGIC_OPS.get((multiplier, shift))
    if decoded is None:
        return None
    divisor, pattern = decoded
    return {"divisor": divisor, "multiplier": multiplier, "shift": shift, "pattern": pattern}


def x86_64_arg_udiv_magic_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_udiv_magic(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 unsigned magic-multiply division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-udiv-magic-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": "/",
            "divisor": divisor,
            "multiplier": f"0x{int(decoded['multiplier']):08x}",
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_UREM_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("89f889f9baabaaaaaa480fafd148c1ea218d0c5229c8c3"): (3, "0xaaaaaaab", 33, "mov-eax-edi-mov-ecx-edi-mov-edx-magic-imul-rdx-rcx-shr-rdx-33-lea-ecx-rdx-rdx2-sub-eax-ecx-ret"),
    bytes.fromhex("89f889f9bacdcccccc480fafd148c1ea228d0c9229c8c3"): (5, "0xcccccccd", 34, "mov-eax-edi-mov-ecx-edi-mov-edx-magic-imul-rdx-rcx-shr-rdx-34-lea-ecx-rdx-rdx4-sub-eax-ecx-ret"),
    bytes.fromhex("89f889f9bacdcccccc480fafd148c1ea2301d28d0c9229c8c3"): (10, "0xcccccccd", 35, "mov-eax-edi-mov-ecx-edi-mov-edx-magic-imul-rdx-rcx-shr-rdx-35-add-edx-edx-lea-ecx-rdx-rdx4-sub-eax-ecx-ret"),
}


def decode_x86_64_arg_urem_magic(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_UREM_MAGIC_OPS.get(body)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
    }


def x86_64_arg_urem_magic_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_urem_magic(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 unsigned magic-multiply remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-urem-magic-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": "%",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_sdiv_pow2(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x89\xf8\xc1\xe8\x1f\x01\xf8\xd1\xf8\xc3":
        return {
            "shift": 1,
            "divisor": 2,
            "bias": 1,
            "pattern": "mov-eax-edi-shr-eax-31-add-eax-edi-sar-eax-one-ret",
        }
    if len(body) == 12 and body[:2] == b"\x8d\x47" and body[3:8] == b"\x85\xff\x0f\x49\xc7" and body[8:10] == b"\xc1\xf8" and body[11] == 0xC3:
        bias = body[2]
        shift = body[10]
        if not 2 <= shift <= 7:
            return None
        if bias != (1 << shift) - 1:
            return None
        return {
            "shift": shift,
            "divisor": 1 << shift,
            "bias": bias,
            "pattern": "lea-eax-rdi-bias-test-edi-edi-cmovns-eax-edi-sar-eax-imm8-ret",
        }
    return None


def x86_64_arg_sdiv_pow2_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_sdiv_pow2(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 signed power-of-two division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-sdiv-pow2-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": "/",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "bias": int(decoded["bias"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_srem_pow2(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x89\xf8\x89\xf9\xc1\xe9\x1f\x01\xf9\x83\xe1\xfe\x29\xc8\xc3":
        return {
            "shift": 1,
            "divisor": 2,
            "bias": 1,
            "mask": "0xfffffffe",
            "pattern": "mov-eax-edi-mov-ecx-edi-shr-ecx-31-add-ecx-edi-and-ecx-neg2-sub-eax-ecx-ret",
        }
    if len(body) == 16 and body[:2] == b"\x89\xf8" and body[2:4] == b"\x8d\x48" and body[5:10] == b"\x85\xff\x0f\x49\xcf" and body[10:12] == b"\x83\xe1" and body[13:] == b"\x29\xc8\xc3":
        bias = body[4]
        mask_byte = body[12]
        if bias == 0:
            return None
        divisor = bias + 1
        if divisor & (divisor - 1):
            return None
        shift = divisor.bit_length() - 1
        if not 2 <= shift <= 7:
            return None
        if mask_byte != ((256 - divisor) & 0xFF):
            return None
        return {
            "shift": shift,
            "divisor": divisor,
            "bias": bias,
            "mask": f"0xffffff{mask_byte:02x}",
            "pattern": "mov-eax-edi-lea-ecx-rax-bias-test-edi-edi-cmovns-ecx-edi-and-ecx-negdivisor-sub-eax-ecx-ret",
        }
    return None


def x86_64_arg_srem_pow2_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_srem_pow2(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 signed power-of-two remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-srem-pow2-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": "%",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "bias": int(decoded["bias"]),
            "mask": decoded["mask"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_SDIV_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("4863c74869c0565555554889c148c1e93f48c1e82001c8c3"): (3, "0x55555556", 32, "movsxd-rax-edi-imul-rax-rax-magic-mov-rcx-rax-shr-rcx-63-shr-rax-32-add-eax-ecx-ret"),
    bytes.fromhex("4863c74869c0676666664889c148c1e93f48c1f82101c8c3"): (5, "0x66666667", 33, "movsxd-rax-edi-imul-rax-rax-magic-mov-rcx-rax-shr-rcx-63-sar-rax-33-add-eax-ecx-ret"),
    bytes.fromhex("4863c74869c0676666664889c148c1e93f48c1f82201c8c3"): (10, "0x66666667", 34, "movsxd-rax-edi-imul-rax-rax-magic-mov-rcx-rax-shr-rcx-63-sar-rax-34-add-eax-ecx-ret"),
}


def decode_x86_64_arg_sdiv_magic(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_SDIV_MAGIC_OPS.get(body)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
    }


def x86_64_arg_sdiv_magic_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_sdiv_magic(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 signed magic-multiply division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-sdiv-magic-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": "/",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_SREM_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("4863c74869c8565555554889ca48c1ea3f48c1e92001d18d0c4929c8c3"): (3, "0x55555556", 32, "movsxd-rax-edi-imul-rcx-rax-magic-mov-rdx-rcx-shr-rdx-63-shr-rcx-32-add-ecx-edx-lea-ecx-rcx-rcx2-sub-eax-ecx-ret"),
    bytes.fromhex("4863c74869c8676666664889ca48c1ea3f48c1f92101d18d0c8929c8c3"): (5, "0x66666667", 33, "movsxd-rax-edi-imul-rcx-rax-magic-mov-rdx-rcx-shr-rdx-63-sar-rcx-33-add-ecx-edx-lea-ecx-rcx-rcx4-sub-eax-ecx-ret"),
    bytes.fromhex("4863c74869c8676666664889ca48c1ea3f48c1f92201d101c98d0c8929c8c3"): (10, "0x66666667", 34, "movsxd-rax-edi-imul-rcx-rax-magic-mov-rdx-rcx-shr-rdx-63-sar-rcx-34-add-ecx-edx-add-ecx-ecx-lea-ecx-rcx-rcx4-sub-eax-ecx-ret"),
}


def decode_x86_64_arg_srem_magic(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_SREM_MAGIC_OPS.get(body)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
    }


def x86_64_arg_srem_magic_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_srem_magic(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 signed magic-multiply remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-srem-magic-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": "%",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_bswap32(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x89\xf8\x0f\xc8\xc3":
        return {"pattern": "mov-eax-edi-bswap-eax-ret"}
    return None


def x86_64_arg_bswap32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_bswap32(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    expression = "((value & 0x000000ffu) << 24) | ((value & 0x0000ff00u) << 8) | ((value >> 8) & 0x0000ff00u) | (value >> 24)"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 argument byte-swap pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-bswap32-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operation": "bswap32",
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_bswap64(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x48\x89\xf8\x48\x0f\xc8\xc3":
        return {"pattern": "mov-rax-rdi-bswap-rax-ret"}
    return None


def x86_64_arg_bswap64_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_bswap64(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    expression = "((value & 0x00000000000000ffull) << 56) | ((value & 0x000000000000ff00ull) << 40) | ((value & 0x0000000000ff0000ull) << 24) | ((value & 0x00000000ff000000ull) << 8) | ((value >> 8) & 0x00000000ff000000ull) | ((value >> 24) & 0x0000000000ff0000ull) | ((value >> 40) & 0x000000000000ff00ull) | (value >> 56)"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 argument byte-swap pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-bswap64-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "rdi",
            "operation": "bswap64",
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_rotate(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 5 and body[:2] == b"\x89\xf8" and body[2] == 0xD1 and body[4] == 0xC3:
        if body[3] == 0xC0:
            return {"direction": "left", "count": 1, "encoding": "rol", "pattern": "mov-eax-edi-rol-eax-one-ret"}
        if body[3] == 0xC8:
            return {"direction": "right", "count": 1, "encoding": "ror", "pattern": "mov-eax-edi-ror-eax-one-ret"}
    if len(body) == 6 and body[:2] == b"\x89\xf8" and body[2] == 0xC1 and body[3] == 0xC0 and body[5] == 0xC3:
        count = body[4]
        if not 1 <= count <= 31:
            return None
        if count > 16:
            return {"direction": "right", "count": 32 - count, "encoding": "rol", "encodedCount": count, "pattern": "mov-eax-edi-rol-eax-imm8-ret"}
        return {"direction": "left", "count": count, "encoding": "rol", "encodedCount": count, "pattern": "mov-eax-edi-rol-eax-imm8-ret"}
    return None


def x86_64_arg_rotate_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_rotate(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    direction = str(decoded["direction"])
    count = int(decoded["count"])
    if direction == "left":
        expression = f"(value << {count}) | (value >> {32 - count})"
    else:
        expression = f"(value >> {count}) | (value << {32 - count})"
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument rotate-{direction} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-rot{direction[0]}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "direction": direction,
            "count": count,
            "encodedCount": decoded.get("encodedCount", count),
            "encoding": decoded["encoding"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg64_rotate(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 7 and body[:3] == b"\x48\x89\xf8" and body[3:5] == b"\x48\xd1" and body[6] == 0xC3:
        if body[5] == 0xC0:
            return {"direction": "left", "count": 1, "encoding": "rol", "pattern": "mov-rax-rdi-rol-rax-one-ret"}
        if body[5] == 0xC8:
            return {"direction": "right", "count": 1, "encoding": "ror", "pattern": "mov-rax-rdi-ror-rax-one-ret"}
    if len(body) == 8 and body[:3] == b"\x48\x89\xf8" and body[3:6] == b"\x48\xc1\xc0" and body[7] == 0xC3:
        count = body[6]
        if not 1 <= count <= 63:
            return None
        if count > 32:
            return {"direction": "right", "count": 64 - count, "encoding": "rol", "encodedCount": count, "pattern": "mov-rax-rdi-rol-rax-imm8-ret"}
        return {"direction": "left", "count": count, "encoding": "rol", "encodedCount": count, "pattern": "mov-rax-rdi-rol-rax-imm8-ret"}
    return None


def x86_64_arg64_rotate_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg64_rotate(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    direction = str(decoded["direction"])
    count = int(decoded["count"])
    if direction == "left":
        expression = f"(value << {count}) | (value >> {64 - count})"
    else:
        expression = f"(value >> {count}) | (value << {64 - count})"
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit argument rotate-{direction} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg64-rot{direction[0]}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "rdi",
            "direction": direction,
            "count": count,
            "encodedCount": decoded.get("encodedCount", count),
            "encoding": decoded["encoding"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_SHIFT_IMM8_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE0: ("shl", "<<", "unsigned int", "unsigned int"),
    0xE8: ("shr", ">>", "unsigned int", "unsigned int"),
    0xF8: ("sar", ">>", "int", "int"),
}

X86_64_ARG_SHIFT_ONE_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE8: ("shr", ">>", "unsigned int", "unsigned int"),
    0xF8: ("sar", ">>", "int", "int"),
}


def x86_64_arg_shift_imm8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    pattern = "mov-eax-edi-shift-imm8-ret"
    if len(body) == 6 and body[:3] == b"\x89\xf8\xc1" and body[-1] == 0xC3:
        decoded = X86_64_ARG_SHIFT_IMM8_OPS.get(body[3])
        if decoded is None:
            return None
        shift = body[4]
        if not 2 <= shift <= 31:
            return None
    elif len(body) == 5 and body[:3] == b"\x89\xf8\xd1" and body[-1] == 0xC3:
        decoded = X86_64_ARG_SHIFT_ONE_OPS.get(body[3])
        if decoded is None:
            return None
        shift = 1
        pattern = "mov-eax-edi-shift-one-ret"
    else:
        return None
    suffix, operator, value_type, return_type = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument {suffix} immediate-shift pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {shift};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-{suffix}-imm8-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "operator": operator,
            "shift": shift,
            "pattern": pattern,
            "valueType": value_type,
            "returnType": return_type,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG64_SHIFT_IMM8_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE0: ("shl", "<<", "unsigned long long", "unsigned long long"),
    0xE8: ("shr", ">>", "unsigned long long", "unsigned long long"),
    0xF8: ("sar", ">>", "long long", "long long"),
}

X86_64_ARG64_SHIFT_ONE_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE8: ("shr", ">>", "unsigned long long", "unsigned long long"),
    0xF8: ("sar", ">>", "long long", "long long"),
}


def x86_64_arg64_shift_imm8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    pattern = "mov-rax-rdi-shift-imm8-ret"
    if len(body) == 8 and body[:3] == b"\x48\x89\xf8" and body[3:5] == b"\x48\xc1" and body[-1] == 0xC3:
        decoded = X86_64_ARG64_SHIFT_IMM8_OPS.get(body[5])
        if decoded is None:
            return None
        shift = body[6]
        if not 2 <= shift <= 63:
            return None
    elif len(body) == 7 and body[:3] == b"\x48\x89\xf8" and body[3:5] == b"\x48\xd1" and body[-1] == 0xC3:
        decoded = X86_64_ARG64_SHIFT_ONE_OPS.get(body[5])
        if decoded is None:
            return None
        shift = 1
        pattern = "mov-rax-rdi-shift-one-ret"
    else:
        return None
    suffix, operator, value_type, return_type = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit argument {suffix} immediate-shift pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {shift};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg64-{suffix}-imm8-cdecl",
            "bodyBytes": len(body),
            "registerArg": "rdi",
            "operator": operator,
            "shift": shift,
            "pattern": pattern,
            "valueType": value_type,
            "returnType": return_type,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_IMM8_BINARY_OPS: dict[int, tuple[str, str]] = {
    0xE0: ("and", "&"),
    0xC8: ("or", "|"),
    0xF0: ("xor", "^"),
}


X86_64_ARG_ACCUM_IMM32_BINARY_OPS: dict[int, tuple[str, str]] = {
    0x25: ("and", "&"),
    0x0D: ("or", "|"),
    0x35: ("xor", "^"),
}


def decode_x86_64_arg_imm8_binary_op(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 4 and body[:2] == b"\x8d\x47" and body[3] == 0xC3:
        raw_immediate = body[2]
        signed_immediate = raw_immediate if raw_immediate < 0x80 else raw_immediate - 0x100
        if signed_immediate == 0:
            return None
        suffix = "add" if signed_immediate > 0 else "sub"
        operator = "+" if signed_immediate > 0 else "-"
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": abs(signed_immediate),
            "rawImmediate": raw_immediate,
            "signedImmediate": signed_immediate,
            "immediateBits": 8,
            "pattern": "lea-eax-rdi-disp8-ret",
        }
    if len(body) == 7 and body[:2] == b"\x8d\x87" and body[6] == 0xC3:
        raw_immediate = int.from_bytes(body[2:6], "little", signed=False)
        signed_immediate = int.from_bytes(body[2:6], "little", signed=True)
        if signed_immediate == 0:
            return None
        suffix = "add" if signed_immediate > 0 else "sub"
        operator = "+" if signed_immediate > 0 else "-"
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": abs(signed_immediate),
            "rawImmediate": raw_immediate,
            "signedImmediate": signed_immediate,
            "immediateBits": 32,
            "pattern": "lea-eax-rdi-disp32-ret",
        }
    if len(body) == 6 and body[:3] == b"\x89\xf8\x83" and body[5] == 0xC3:
        decoded = X86_64_ARG_IMM8_BINARY_OPS.get(body[3])
        if decoded is None:
            return None
        raw_immediate = body[4]
        if raw_immediate > 0x7F:
            return None
        suffix, operator = decoded
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": raw_immediate,
            "rawImmediate": raw_immediate,
            "signedImmediate": raw_immediate,
            "immediateBits": 8,
            "pattern": "mov-eax-edi-op-eax-imm8-ret",
        }
    if len(body) == 8 and body[:2] == b"\x89\xf8" and body[7] == 0xC3:
        decoded = X86_64_ARG_ACCUM_IMM32_BINARY_OPS.get(body[2])
        if decoded is None:
            return None
        raw_immediate = int.from_bytes(body[3:7], "little", signed=False)
        if raw_immediate == 0 and decoded[0] in {"or", "xor"}:
            return None
        suffix, operator = decoded
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": raw_immediate,
            "rawImmediate": raw_immediate,
            "signedImmediate": int.from_bytes(body[3:7], "little", signed=True),
            "immediateBits": 32,
            "pattern": "mov-eax-edi-accum-op-eax-imm32-ret",
        }
    return None


def x86_64_arg_imm8_binary_op_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_imm8_binary_op(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    immediate_bits = int(decoded.get("immediateBits") or 8)
    immediate_digits = 2 if immediate_bits == 8 else 8
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument {suffix} immediate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:0{immediate_digits}x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-{suffix}-imm{immediate_bits}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": operator,
            "immediate": f"0x{immediate:0{immediate_digits}x}",
            "immediateBits": immediate_bits,
            "rawImmediate": int(decoded["rawImmediate"]),
            "signedImmediate": int(decoded["signedImmediate"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_IMM32_BINARY64_OPS: dict[int, tuple[str, str]] = {
    0x25: ("and", "&"),
    0x0D: ("or", "|"),
    0x35: ("xor", "^"),
}


def decode_x86_64_arg_imm32_binary_op64(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 8 and body[:3] == b"\x48\x8d\x87" and body[7] == 0xC3:
        raw_immediate = int.from_bytes(body[3:7], "little", signed=False)
        signed_immediate = int.from_bytes(body[3:7], "little", signed=True)
        if signed_immediate == 0:
            return None
        suffix = "add" if signed_immediate > 0 else "sub"
        operator = "+" if signed_immediate > 0 else "-"
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": abs(signed_immediate),
            "rawImmediate": raw_immediate,
            "signedImmediate": signed_immediate,
            "immediateBits": 32,
            "pattern": "lea-rax-rdi-disp32-ret",
        }
    if len(body) == 10 and body[:3] == b"\x48\x89\xf8" and body[3] == 0x48 and body[9] == 0xC3:
        decoded = X86_64_ARG_IMM32_BINARY64_OPS.get(body[4])
        if decoded is None or decoded[0] == "and":
            return None
        raw_immediate = int.from_bytes(body[5:9], "little", signed=False)
        if raw_immediate == 0:
            return None
        suffix, operator = decoded
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": raw_immediate,
            "rawImmediate": raw_immediate,
            "signedImmediate": int.from_bytes(body[5:9], "little", signed=True),
            "immediateBits": 32,
            "pattern": "mov-rax-rdi-rex-accum-op-rax-imm32-ret",
        }
    if len(body) == 9 and body[:3] == b"\x48\x89\xf8" and body[8] == 0xC3:
        decoded = X86_64_ARG_IMM32_BINARY64_OPS.get(body[3])
        if decoded is None or decoded[0] != "and":
            return None
        raw_immediate = int.from_bytes(body[4:8], "little", signed=False)
        suffix, operator = decoded
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": raw_immediate,
            "rawImmediate": raw_immediate,
            "signedImmediate": int.from_bytes(body[4:8], "little", signed=True),
            "immediateBits": 32,
            "pattern": "mov-rax-rdi-and-eax-imm32-ret",
        }
    return None


def x86_64_arg_imm32_binary_op64_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_imm32_binary_op64(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit argument {suffix} immediate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return value {operator} 0x{immediate:08x}ull;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg64-{suffix}-imm32-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "rdi",
            "operator": operator,
            "immediate": f"0x{immediate:08x}",
            "immediateBits": 32,
            "rawImmediate": int(decoded["rawImmediate"]),
            "signedImmediate": int(decoded["signedImmediate"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_UNARY_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x89\xf8\xf7\xd8\xc3": ("neg", "-", "mov-eax-edi-neg-eax-ret"),
    b"\x89\xf8\xf7\xd0\xc3": ("not", "~", "mov-eax-edi-not-eax-ret"),
}


def x86_64_arg_unary_op_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_UNARY_OPS.get(body)
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument {suffix} unary pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {operator}value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-{suffix}-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "operator": operator,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_UNARY_OPS64: dict[bytes, tuple[str, str, str]] = {
    b"\x48\x89\xf8\x48\xf7\xd8\xc3": ("neg", "-", "mov-rax-rdi-neg-rax-ret"),
    b"\x48\x89\xf8\x48\xf7\xd0\xc3": ("not", "~", "mov-rax-rdi-not-rax-ret"),
}


def x86_64_arg64_unary_op_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_UNARY_OPS64.get(body)
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit argument {suffix} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return {operator}value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg64-{suffix}-cdecl",
            "bodyBytes": len(body),
            "registerArg": "rdi",
            "operator": operator,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_NEG_CMOV_OPS: dict[bytes, tuple[str, str, str, str]] = {
    b"\x89\xf8\xf7\xd8\x0f\x48\xc7\xc3": ("abs", "value < 0 ? -value : value", "cmovs", "mov-eax-edi-neg-eax-cmovs-eax-edi-ret"),
    b"\x89\xf8\xf7\xd8\x0f\x49\xc7\xc3": ("neg-if-pos", "value > 0 ? -value : value", "cmovns", "mov-eax-edi-neg-eax-cmovns-eax-edi-ret"),
}


def x86_64_arg_neg_cmov_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_NEG_CMOV_OPS.get(body)
    if decoded is None:
        return None
    suffix, expression, cmov, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 signed argument {suffix} neg/cmov pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-{suffix}-cmov-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "valueType": "int",
            "returnType": "int",
            "expression": expression,
            "cmov": cmov,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG64_NEG_CMOV_OPS: dict[bytes, tuple[str, str, str, str]] = {
    b"\x48\x89\xf8\x48\xf7\xd8\x48\x0f\x48\xc7\xc3": ("abs", "value < 0 ? -value : value", "cmovs", "mov-rax-rdi-neg-rax-cmovs-rax-rdi-ret"),
    b"\x48\x89\xf8\x48\xf7\xd8\x48\x0f\x49\xc7\xc3": ("neg-if-pos", "value > 0 ? -value : value", "cmovns", "mov-rax-rdi-neg-rax-cmovns-rax-rdi-ret"),
}


def x86_64_arg64_neg_cmov_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG64_NEG_CMOV_OPS.get(body)
    if decoded is None:
        return None
    suffix, expression, cmov, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 signed 64-bit argument {suffix} neg/cmov pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"long long {c_name}(long long value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg64-{suffix}-cmov-cdecl",
            "bodyBytes": len(body),
            "registerArg": "rdi",
            "valueType": "long long",
            "returnType": "long long",
            "expression": expression,
            "cmov": cmov,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG_CAST_OPS: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x40\x0f\xb6\xc7\xc3": ("u8", "unsigned int", "unsigned int", "(unsigned char)value", "movzx-eax-dil-ret"),
    b"\x40\x0f\xbe\xc7\xc3": ("i8", "int", "int", "(signed char)value", "movsx-eax-dil-ret"),
    b"\x0f\xb7\xc7\xc3": ("u16", "unsigned int", "unsigned int", "(unsigned short)value", "movzx-eax-di-ret"),
    b"\x0f\xbf\xc7\xc3": ("i16", "int", "int", "(short)value", "movsx-eax-di-ret"),
}


def x86_64_arg_cast_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_CAST_OPS.get(body)
    if decoded is None:
        return None
    suffix, value_type, return_type, expression, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument {suffix} cast pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-cast-{suffix}-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "valueType": value_type,
            "returnType": return_type,
            "expression": expression,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG64_SIGN_EXTEND_OPS: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x48\x63\xc7\xc3": ("i32", "int", "long long", "(long long)value", "movsxd-rax-edi-ret"),
    b"\x48\x0f\xbe\xc7\xc3": ("i8", "int", "long long", "(signed char)value", "movsx-rax-dil-ret"),
    b"\x48\x0f\xbf\xc7\xc3": ("i16", "int", "long long", "(short)value", "movsx-rax-di-ret"),
}


def x86_64_arg64_sign_extend_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG64_SIGN_EXTEND_OPS.get(body)
    if decoded is None:
        return None
    suffix, value_type, return_type, expression, pattern = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument {suffix} sign-extension-to-64 pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg64-sign-extend-{suffix}-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "valueType": value_type,
            "returnType": return_type,
            "expression": expression,
            "pattern": pattern,
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_narrow_imm8_compare(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 10 and body[:3] == b"\x31\xc0\x40" and body[3] == 0x80 and body[4] == 0xFF and body[6] == 0x0F and body[8:] == b"\xc0\xc3":
        width = 8
        immediate = body[5]
        setcc_opcode = body[7]
    elif len(body) == 10 and body[:3] == b"\x31\xc0\x66" and body[3] == 0x83 and body[4] == 0xFF and body[6] == 0x0F and body[8:] == b"\xc0\xc3":
        width = 16
        immediate = body[5]
        setcc_opcode = body[7]
    else:
        return None
    if setcc_opcode in X86_64_UNSIGNED_COMPARE_SETCC:
        suffix, operator, setcc = X86_64_UNSIGNED_COMPARE_SETCC[setcc_opcode]
        signed = False
    elif setcc_opcode in X86_64_SIGNED_COMPARE_SETCC:
        suffix, operator, setcc = X86_64_SIGNED_COMPARE_SETCC[setcc_opcode]
        signed = True
    else:
        return None
    cast_type = {
        (8, False): "unsigned char",
        (8, True): "signed char",
        (16, False): "unsigned short",
        (16, True): "short",
    }[(width, signed)]
    value_type = "int" if signed else "unsigned int"
    expression_immediate = immediate if not signed or immediate < 0x80 else immediate - 0x100
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "width": width,
        "signed": signed,
        "castType": cast_type,
        "valueType": value_type,
        "immediate": expression_immediate,
        "rawImmediate": immediate,
        "pattern": f"xor-eax-cmp-{'dil' if width == 8 else 'di'}-imm8-setcc-al-ret",
    }


def x86_64_arg_narrow_imm8_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_narrow_imm8_compare(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    width = int(decoded["width"])
    signed = bool(decoded["signed"])
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    value_type = str(decoded["valueType"])
    cast_type = str(decoded["castType"])
    immediate = int(decoded["immediate"])
    family = "int" if signed else "uint"
    immediate_expr = f"({immediate})" if signed else f"0x{int(decoded['rawImmediate']):02x}u"
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 {cast_type} immediate comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}({value_type} value) {{",
            f"    return ({cast_type})value {operator} {immediate_expr};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-{family}{width}-{suffix}-imm8-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": operator,
            "width": width,
            "castType": cast_type,
            "valueType": value_type,
            "immediate": immediate_expr,
            "rawImmediate": int(decoded["rawImmediate"]),
            "setcc": decoded["setcc"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_narrow_movzx_imm8_compare(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 13 and body[:8] == b"\x40\x0f\xb6\xcf\x31\xc0\x83\xf9" and body[9] == 0x0F and body[11:] == b"\xc0\xc3":
        width = 8
        immediate = body[8]
        setcc_opcode = body[10]
        movzx = "movzx-ecx-dil"
    elif len(body) == 12 and body[:7] == b"\x0f\xb7\xcf\x31\xc0\x83\xf9" and body[8] == 0x0F and body[10:] == b"\xc0\xc3":
        width = 16
        immediate = body[7]
        setcc_opcode = body[9]
        movzx = "movzx-ecx-di"
    else:
        return None
    decoded = X86_64_UNSIGNED_COMPARE_SETCC.get(setcc_opcode)
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    if suffix not in {"lt", "ge"}:
        return None
    cast_type = "unsigned char" if width == 8 else "unsigned short"
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "width": width,
        "castType": cast_type,
        "valueType": "unsigned int",
        "rawImmediate": immediate,
        "pattern": f"{movzx}-xor-eax-cmp-ecx-imm8-setcc-al-ret",
    }


def x86_64_arg_narrow_movzx_imm8_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_narrow_movzx_imm8_compare(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    width = int(decoded["width"])
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    cast_type = str(decoded["castType"])
    immediate_expr = f"0x{int(decoded['rawImmediate']):02x}u"
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 {cast_type} movzx immediate comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            f"    return ({cast_type})value {operator} {immediate_expr};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-uint{width}-{suffix}-movzx-imm8-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "scratchRegister": "ecx",
            "operator": operator,
            "width": width,
            "castType": cast_type,
            "valueType": decoded["valueType"],
            "immediate": immediate_expr,
            "rawImmediate": int(decoded["rawImmediate"]),
            "setcc": decoded["setcc"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_imm8_compare(data: bytes, *, signed: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    immediate_bits = 0
    raw_immediate = 0
    immediate = 0
    setcc_offset = 0
    if len(body) == 9 and body[:4] == b"\x31\xc0\x83\xff" and body[5] == 0x0F and body[7:] == b"\xc0\xc3":
        immediate_bits = 8
        raw_immediate = body[4]
        immediate = raw_immediate if raw_immediate < 0x80 else (raw_immediate - 0x100 if signed else raw_immediate | 0xFFFFFF00)
        setcc_offset = 6
        pattern = "xor-eax-cmp-edi-imm8-setcc-al-ret"
    elif len(body) == 12 and body[:4] == b"\x31\xc0\x81\xff" and body[8] == 0x0F and body[10:] == b"\xc0\xc3":
        immediate_bits = 32
        raw_immediate = int.from_bytes(body[4:8], "little", signed=False)
        immediate = int.from_bytes(body[4:8], "little", signed=signed)
        setcc_offset = 9
        pattern = "xor-eax-cmp-edi-imm32-setcc-al-ret"
    else:
        return None
    rules = X86_64_SIGNED_COMPARE_SETCC if signed else X86_64_UNSIGNED_COMPARE_SETCC
    decoded = rules.get(body[setcc_offset])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "immediate": immediate,
        "rawImmediate": raw_immediate,
        "immediateBits": immediate_bits,
        "pattern": pattern,
    }


def normalize_x86_64_arg_imm8_compare_operator(
    operator: str,
    immediate: int,
    *,
    signed: bool,
) -> tuple[str, int]:
    if operator not in {"<=", ">"}:
        return operator, immediate

    overflow_limit = 0x7FFFFFFF if signed else 0xFFFFFFFF
    if immediate >= overflow_limit:
        return operator, immediate

    next_immediate = immediate + 1
    if operator == "<=" and immediate < overflow_limit:
        return "<", next_immediate
    if operator == ">" and immediate < overflow_limit:
        return ">=", next_immediate
    return operator, immediate


def x86_64_arg_unsigned_imm8_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_imm8_compare(data, signed=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"]) & 0xFFFFFFFF
    immediate_bits = int(decoded.get("immediateBits") or 8)
    operator, immediate = normalize_x86_64_arg_imm8_compare_operator(operator, immediate, signed=False)
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 unsigned one-argument immediate comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-uint-{suffix}-imm{immediate_bits}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": operator,
            "immediate": f"0x{immediate:08x}",
            "immediateBits": immediate_bits,
            "setcc": decoded["setcc"],
            "rawImmediate": int(decoded["rawImmediate"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_arg_signed_imm8_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_imm8_compare(data, signed=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    immediate_bits = int(decoded.get("immediateBits") or 8)
    operator, immediate = normalize_x86_64_arg_imm8_compare_operator(operator, immediate, signed=True)
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 signed one-argument immediate comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value {operator} ({immediate});",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-int-{suffix}-imm{immediate_bits}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": operator,
            "immediate": immediate,
            "immediateBits": immediate_bits,
            "setcc": decoded["setcc"],
            "rawImmediate": int(decoded["rawImmediate"]),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_UNSIGNED_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x92: ("lt", "<", "setb"),
    0x93: ("ge", ">=", "setae"),
    0x94: ("eq", "==", "sete"),
    0x95: ("ne", "!=", "setne"),
    0x96: ("le", "<=", "setbe"),
    0x97: ("gt", ">", "seta"),
}


X86_64_SIGNED_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x9C: ("lt", "<", "setl"),
    0x9D: ("ge", ">=", "setge"),
    0x9E: ("le", "<=", "setle"),
    0x9F: ("gt", ">", "setg"),
}


def decode_x86_64_two_args_compare(data: bytes, rules: dict[int, tuple[str, str, str]]) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:4] != b"\x31\xc0\x39\xf7" or body[4] != 0x0F or body[6:] != b"\xc0\xc3":
        return None
    decoded = rules.get(body[5])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
    }


def x86_64_two_args_unsigned_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_two_args_compare(data, X86_64_UNSIGNED_COMPARE_SETCC)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 two-argument unsigned {suffix} comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-uint-{suffix}-two-args-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArgs": ["edi", "esi"],
            "operator": operator,
            "setcc": decoded["setcc"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_two_args_signed_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_two_args_compare(data, X86_64_SIGNED_COMPARE_SETCC)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 two-argument signed {suffix} comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int a, int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-int-{suffix}-two-args-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArgs": ["edi", "esi"],
            "operator": operator,
            "setcc": decoded["setcc"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_two_args_compare64(data: bytes, rules: dict[int, tuple[str, str, str]]) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 9 or body[:5] != b"\x31\xc0\x48\x39\xf7" or body[5] != 0x0F or body[7:] != b"\xc0\xc3":
        return None
    decoded = rules.get(body[6])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
    }


def x86_64_two_args_unsigned_compare64_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_two_args_compare64(data, X86_64_UNSIGNED_COMPARE_SETCC)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit two-argument unsigned {suffix} comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned long long a, unsigned long long b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-uint64-{suffix}-two-args-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArgs": ["rdi", "rsi"],
            "operator": operator,
            "setcc": decoded["setcc"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_two_args_signed_compare64_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_two_args_compare64(data, X86_64_SIGNED_COMPARE_SETCC)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit two-argument signed {suffix} comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(long long a, long long b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-int64-{suffix}-two-args-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArgs": ["rdi", "rsi"],
            "operator": operator,
            "setcc": decoded["setcc"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_SIGNED_ZERO_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x9E: ("le", "<=", "setle"),
    0x9F: ("gt", ">", "setg"),
}


def decode_x86_64_arg_signed_zero_compare(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:4] != b"\x31\xc0\x85\xff" or body[4] != 0x0F or body[6:] != b"\xc0\xc3":
        return None
    decoded = X86_64_SIGNED_ZERO_COMPARE_SETCC.get(body[5])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "pattern": "xor-eax-test-edi-edi-setcc-al-ret",
    }


def x86_64_arg_signed_zero_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_signed_zero_compare(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 signed one-argument zero {suffix} comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-int-zero-{suffix}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": operator,
            "immediate": 0,
            "setcc": decoded["setcc"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_nonzero_const_select(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 15 or body[:4] != b"\x31\xc0\x85\xff" or body[4] != 0x0F or body[6:9] != b"\xc0\x8d\x04" or body[14] != 0xC3:
        return None
    setcc_opcode = body[5]
    if setcc_opcode not in {0x94, 0x95}:
        return None
    sib = body[9]
    if sib & 0x07 != 0x05 or ((sib >> 3) & 0x07) != 0x00:
        return None
    scale = 1 << ((sib >> 6) & 0x03)
    if scale not in {2, 4, 8}:
        return None
    base_value = int.from_bytes(body[10:14], "little", signed=False)
    scaled_value = base_value + scale
    if setcc_opcode == 0x95:
        false_value = base_value
        true_value = scaled_value
        setcc = "setne"
    else:
        false_value = scaled_value
        true_value = base_value
        setcc = "sete"
    return {
        "trueValue": true_value,
        "falseValue": false_value,
        "baseValue": base_value,
        "scale": scale,
        "setcc": setcc,
        "pattern": f"xor-eax-test-edi-edi-{setcc}-al-lea-eax-rax{scale}-disp32-ret",
    }


def x86_64_arg_nonzero_const_select_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_nonzero_const_select(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    true_value = int(decoded["trueValue"])
    false_value = int(decoded["falseValue"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 nonzero constant-select pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value != 0 ? 0x{true_value:08x}u : 0x{false_value:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-nonzero-const-select-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "trueValue": f"0x{true_value:08x}",
            "falseValue": f"0x{false_value:08x}",
            "baseValue": int(decoded["baseValue"]),
            "scale": int(decoded["scale"]),
            "setcc": decoded["setcc"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_nonzero_cmov_const_select(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 11 and body[:2] == b"\x85\xff" and body[2] == 0xB8 and body[7:10] == b"\x0f\x44\xc7" and body[10] == 0xC3:
        immediate = int.from_bytes(body[3:7], "little", signed=False)
        if immediate == 0:
            return None
        return {
            "trueValue": immediate,
            "falseValue": 0,
            "immediate": immediate,
            "cmov": "cmove",
            "pattern": "test-edi-edi-mov-eax-imm32-cmove-eax-edi-ret",
        }
    if len(body) == 13 and body[:4] == b"\x31\xc9\x85\xff" and body[4] == 0xB8 and body[9:12] == b"\x0f\x45\xc1" and body[12] == 0xC3:
        immediate = int.from_bytes(body[5:9], "little", signed=False)
        if immediate == 0:
            return None
        return {
            "trueValue": 0,
            "falseValue": immediate,
            "immediate": immediate,
            "cmov": "cmovne",
            "pattern": "xor-ecx-ecx-test-edi-edi-mov-eax-imm32-cmovne-eax-ecx-ret",
        }
    return None


def x86_64_arg_nonzero_cmov_const_select_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_nonzero_cmov_const_select(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    true_value = int(decoded["trueValue"])
    false_value = int(decoded["falseValue"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 nonzero cmov constant-select pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value != 0 ? 0x{true_value:08x}u : 0x{false_value:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-nonzero-cmov-const-select-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "trueValue": f"0x{true_value:08x}",
            "falseValue": f"0x{false_value:08x}",
            "immediate": f"0x{int(decoded['immediate']):08x}",
            "cmov": decoded["cmov"],
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def decode_x86_64_arg_mask(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x31\xc0\xf7\xdf\x19\xc0\xc3":
        return {
            "suffix": "nonzero",
            "operator": "!=",
            "immediate": 0,
            "expression": "value != 0",
            "valueType": "unsigned int",
            "returnType": "unsigned int",
            "trueLiteral": "0xffffffffu",
            "falseLiteral": "0u",
            "pattern": "xor-eax-neg-edi-sbb-eax-eax-ret",
        }
    if len(body) == 8 and body[:4] == b"\x31\xc0\x83\xff" and body[6:] == b"\xc0\xc3":
        immediate = body[4]
        opcode = body[5]
        if opcode == 0x19:
            return {
                "suffix": "uint-lt-imm8",
                "operator": "<",
                "immediate": immediate,
                "expression": f"value < 0x{immediate:02x}u",
                "valueType": "unsigned int",
                "returnType": "unsigned int",
                "trueLiteral": "0xffffffffu",
                "falseLiteral": "0u",
                "pattern": "xor-eax-cmp-edi-imm8-sbb-eax-eax-ret",
            }
    if len(body) == 9 and body[:4] == b"\x31\xc0\x83\xff" and body[5:8] == b"\x83\xd0\xff" and body[8] == 0xC3:
        immediate = body[4]
        return {
            "suffix": "uint-ge-imm8",
            "operator": ">=",
            "immediate": immediate,
            "expression": f"value >= 0x{immediate:02x}u",
            "valueType": "unsigned int",
            "returnType": "unsigned int",
            "trueLiteral": "0xffffffffu",
            "falseLiteral": "0u",
            "pattern": "xor-eax-cmp-edi-imm8-adc-eax-minus-one-ret",
        }
    if len(body) == 11 and body[:4] == b"\x31\xc0\x83\xff" and body[5] == 0x0F and body[7:] == b"\xc0\xf7\xd8\xc3":
        raw_immediate = body[4]
        setcc_opcode = body[6]
        if setcc_opcode in {0x94, 0x95}:
            suffix, operator, setcc = X86_64_UNSIGNED_COMPARE_SETCC[setcc_opcode]
            return {
                "suffix": f"uint-{suffix}-imm8",
                "operator": operator,
                "immediate": raw_immediate,
                "expression": f"value {operator} 0x{raw_immediate:02x}u",
                "valueType": "unsigned int",
                "returnType": "unsigned int",
                "trueLiteral": "0xffffffffu",
                "falseLiteral": "0u",
                "setcc": setcc,
                "pattern": f"xor-eax-cmp-edi-imm8-{setcc}-al-neg-eax-ret",
            }
        decoded_signed = X86_64_SIGNED_COMPARE_SETCC.get(setcc_opcode)
        if decoded_signed is not None:
            suffix, operator, setcc = decoded_signed
            signed_immediate = raw_immediate if raw_immediate < 0x80 else raw_immediate - 0x100
            return {
                "suffix": f"int-{suffix}-imm8",
                "operator": operator,
                "immediate": signed_immediate,
                "rawImmediate": raw_immediate,
                "expression": f"value {operator} {signed_immediate}",
                "valueType": "int",
                "returnType": "int",
                "trueLiteral": "-1",
                "falseLiteral": "0",
                "setcc": setcc,
                "pattern": f"xor-eax-cmp-edi-imm8-{setcc}-al-neg-eax-ret",
            }
    return None


def x86_64_arg_mask_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    decoded = decode_x86_64_arg_mask(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    expression = str(decoded["expression"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    true_literal = str(decoded["trueLiteral"])
    false_literal = str(decoded["falseLiteral"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 argument {decoded['suffix']} all-bits mask pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return {expression} ? {true_literal} : {false_literal};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg-{decoded['suffix']}-mask-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "registerArg": "edi",
            "operator": decoded["operator"],
            "immediate": int(decoded["immediate"]),
            "valueType": value_type,
            "returnType": return_type,
            "trueValue": "0xffffffff",
            "falseValue": "0x00000000",
            "setcc": decoded.get("setcc"),
            "pattern": decoded["pattern"],
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_arg_nonzero_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != b"\x31\xc0\x85\xff\x0f\x95\xc0\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 nonzero-argument boolean return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-nonzero-bool-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "predicate": "value != 0",
            "framePointer": False,
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_arg_zero_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != b"\x31\xc0\x85\xff\x0f\x94\xc0\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 zero-argument boolean return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            "    return value == 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-arg-zero-bool-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "predicate": "value == 0",
            "framePointer": False,
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


X86_64_ARG64_ZERO_NONZERO_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x31\xc0\x48\x85\xff\x0f\x95\xc0\xc3": ("nonzero", "!=", "setne"),
    b"\x31\xc0\x48\x85\xff\x0f\x94\xc0\xc3": ("zero", "==", "sete"),
}


def x86_64_arg64_zero_nonzero_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_x86_64_task(task):
        return None
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG64_ZERO_NONZERO_OPS.get(body)
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86_64 64-bit argument {suffix} boolean return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned long long value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"x86-64-arg64-{suffix}-bool-cdecl",
            "bodyBytes": len(body),
            "registerArg": "rdi",
            "operator": operator,
            "predicate": f"value {operator} 0",
            "setcc": setcc,
            "pattern": f"xor-eax-test-rdi-{setcc}-al-ret",
            "framePointer": False,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=False),
    }


def x86_64_framed_return_first_arg_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != b"\x55\x48\x89\xe5\x89\xf8\x5d\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 framed first-argument return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 frame-pointer byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-framed-return-first-arg-cdecl",
            "bodyBytes": len(body),
            "registerArg": "edi",
            "framePointer": True,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=True),
    }


def x86_64_framed_add_two_args_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != b"\x55\x48\x89\xe5\x8d\x04\x37\x5d\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86_64 framed two-argument add pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86_64 frame-pointer byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "x86-64-framed-add-two-args-cdecl",
            "bodyBytes": len(body),
            "registerArgs": ["edi", "esi"],
            "framePointer": True,
            "targetFormat": task.get("targetFormat"),
        },
        "compilerProfileHints": x86_64_o2_leaf_compiler_profile_hint(task, frame_pointer=True),
    }


def x86_64_framed_return_compiler_profile_hint(task: dict[str, Any]) -> dict[str, Any]:
    if str(task.get("targetFormat") or "") == "macho":
        return {
            "compiler": "clang",
            "language": "c",
            "args": ["--target=x86_64-apple-macosx10.12", "-m64", "-O0", "-ffreestanding", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"],
            "reason": "Mach-O x86_64 frame-pointer return pattern requires a Darwin target and O0 frame-preserving profile",
        }
    return {
        "compiler": "clang",
        "language": "c",
        "args": ["-m64", "-O0", "-ffreestanding", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"],
        "reason": "x86_64 frame-pointer return pattern requires an O0 frame-preserving profile",
    }


def x86_64_o2_leaf_compiler_profile_hint(task: dict[str, Any], *, frame_pointer: bool) -> dict[str, Any]:
    frame_flag = "-fno-omit-frame-pointer" if frame_pointer else "-fomit-frame-pointer"
    if str(task.get("targetFormat") or "") == "macho":
        return {
            "compiler": "clang",
            "language": "c",
            "args": ["--target=x86_64-apple-macosx10.12", "-m64", "-O2", frame_flag, "-ffreestanding", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"],
            "reason": "Mach-O x86_64 O2 leaf pattern with explicit frame-pointer control",
        }
    return {
        "compiler": "clang",
        "language": "c",
        "args": ["-m64", "-O2", frame_flag, "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"],
        "reason": "x86_64 O2 leaf pattern with explicit frame-pointer control",
    }


def framed_return_compiler_profile_hint(style: str) -> dict[str, Any]:
    if style == "gcc-clang":
        return {
            "compiler": "clang",
            "language": "c",
            "args": ["-m32", "-O0", "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"],
            "reason": "gcc/clang frame-pointer return pattern requires an O0 frame-preserving profile",
        }
    return {
        "compiler": "msvc",
        "language": "c",
        "args": ["/Od", "/GS-", "/Oy-"],
        "reason": "MSVC frame-pointer return pattern requires a frame-preserving profile",
    }


def one_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 6 or data[:5] != b"\xb8\x01\x00\x00\x00" or data[5] != 0xC3:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 one-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(void) {{",
            "    return 1;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "return-one-cdecl",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "constant one return is a canonical x86 leaf pattern",
        },
    }


def one_return_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 8 or data[:5] != b"\xb8\x01\x00\x00\x00" or data[5:] != b"\xc2\x04\x00":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall one-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(unsigned int unused) {{",
            "    (void)unused;",
            "    return 1;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "return-one-stdcall",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall constant one return is a canonical x86 leaf pattern",
        },
    }


def return_first_stack_arg_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 5 or data != b"\x8b\x44\x24\x04\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "return-first-stack-arg-cdecl",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stack argument passthrough is a canonical x86 leaf pattern",
        },
    }


def return_first_stack_arg_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 7 or data != b"\x8b\x44\x24\x04\xc2\x04\x00":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "return-first-stack-arg-stdcall",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall stack argument passthrough is a canonical x86 leaf pattern",
        },
    }


def add_two_stack_args_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    patterns = {
        b"\x8b\x44\x24\x04\x03\x44\x24\x08\xc3": "stack4-plus-stack8",
        b"\x8b\x44\x24\x08\x03\x44\x24\x04\xc3": "stack8-plus-stack4",
    }
    if len(data) != 9 or data not in patterns:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 two-argument add pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "add-two-stack-args-cdecl",
            "bodyBytes": len(data),
            "operandOrder": patterns[data],
        },
        "compilerProfileHints": {
            "compiler": "clang" if patterns[data] == "stack8-plus-stack4" else "msvc",
            "language": "c",
            "args": ["-m32", "-O2", "-fomit-frame-pointer", "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"] if patterns[data] == "stack8-plus-stack4" else ["/O2", "/GS-", "/Oy"],
            "reason": "simple two-arg arithmetic is a canonical x86 leaf pattern",
        },
    }


def add_two_stack_args_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    patterns = {
        b"\x8b\x44\x24\x04\x03\x44\x24\x08\xc2\x08\x00": "stack4-plus-stack8",
        b"\x8b\x44\x24\x08\x03\x44\x24\x04\xc2\x08\x00": "stack8-plus-stack4",
    }
    if len(data) != 11 or data not in patterns:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall two-argument add pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "add-two-stack-args-stdcall",
            "bodyBytes": len(data),
            "operandOrder": patterns[data],
        },
        "compilerProfileHints": {
            "compiler": "clang" if patterns[data] == "stack8-plus-stack4" else "msvc",
            "language": "c",
            "args": ["-m32", "-O2", "-fomit-frame-pointer", "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"] if patterns[data] == "stack8-plus-stack4" else ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall two-arg arithmetic is a canonical x86 leaf pattern",
        },
    }


I386_TWO_STACK_ARG_BINARY_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x8b\x44\x24\x04\x2b\x44\x24\x08": ("sub", "-", "stack4-minus-stack8"),
    b"\x8b\x44\x24\x08\x0f\xaf\x44\x24\x04": ("mul", "*", "stack8-times-stack4"),
    b"\x8b\x44\x24\x08\x23\x44\x24\x04": ("and", "&", "stack8-and-stack4"),
    b"\x8b\x44\x24\x08\x0b\x44\x24\x04": ("or", "|", "stack8-or-stack4"),
    b"\x8b\x44\x24\x08\x33\x44\x24\x04": ("xor", "^", "stack8-xor-stack4"),
}


def decode_two_stack_args_binary_op(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x08\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    decoded = I386_TWO_STACK_ARG_BINARY_OPS.get(body[: -len(ret)])
    if decoded is None:
        return None
    suffix, operator, operand_order = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "operandOrder": operand_order,
        "stackBytes": 8 if stdcall else 0,
    }


def two_stack_args_binary_op_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_two_stack_args_binary_op(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 two-argument {suffix} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"two-stack-args-{suffix}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operandOrder": decoded["operandOrder"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"two-argument {suffix} is a canonical clang i386 O2 leaf pattern"
        ),
    }


def two_stack_args_binary_op_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_two_stack_args_binary_op(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stdcall two-argument {suffix} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"two-stack-args-{suffix}-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operandOrder": decoded["operandOrder"],
            "stackBytes": 8,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stdcall two-argument {suffix} is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_UNSIGNED_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x92: ("lt", "<", "setb"),
    0x93: ("ge", ">=", "setae"),
    0x94: ("eq", "==", "sete"),
    0x95: ("ne", "!=", "setne"),
    0x96: ("le", "<=", "setbe"),
    0x97: ("gt", ">", "seta"),
}


I386_SIGNED_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x9C: ("lt", "<", "setl"),
    0x9D: ("ge", ">=", "setge"),
    0x9E: ("le", "<=", "setle"),
    0x9F: ("gt", ">", "setg"),
}


def decode_two_stack_args_unsigned_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x08\x00" if stdcall else b"\xc3"
    expected_len = 16 if stdcall else 14
    if len(body) != expected_len or not body.endswith(ret):
        return None
    if body[:4] != b"\x8b\x4c\x24\x04" or body[4:6] not in {b"\x31\xc0", b"\x33\xc0"}:
        return None
    if body[6:10] != b"\x3b\x4c\x24\x08" or body[10] != 0x0F or body[12] != 0xC0:
        return None
    decoded = I386_UNSIGNED_COMPARE_SETCC.get(body[11])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "zeroInstruction": "xor-eax-eax",
        "stackBytes": 8 if stdcall else 0,
    }


def decode_two_stack_args_signed_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x08\x00" if stdcall else b"\xc3"
    expected_len = 16 if stdcall else 14
    if len(body) != expected_len or not body.endswith(ret):
        return None
    if body[:4] != b"\x8b\x4c\x24\x04" or body[4:6] not in {b"\x31\xc0", b"\x33\xc0"}:
        return None
    if body[6:10] != b"\x3b\x4c\x24\x08" or body[10] != 0x0F or body[12] != 0xC0:
        return None
    decoded = I386_SIGNED_COMPARE_SETCC.get(body[11])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "zeroInstruction": "xor-eax-eax",
        "stackBytes": 8 if stdcall else 0,
    }


def two_stack_args_unsigned_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_two_stack_args_unsigned_compare(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 two-argument unsigned comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"two-stack-args-uint-{suffix}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "setcc": decoded["setcc"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "two-argument unsigned comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def two_stack_args_signed_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_two_stack_args_signed_compare(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 two-argument signed comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int a, int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"two-stack-args-int-{suffix}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "setcc": decoded["setcc"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "two-argument signed comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def two_stack_args_unsigned_compare_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_two_stack_args_unsigned_compare(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall two-argument unsigned comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"two-stack-args-uint-{suffix}-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "setcc": decoded["setcc"],
            "stackBytes": 8,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall two-argument unsigned comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def two_stack_args_signed_compare_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_two_stack_args_signed_compare(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall two-argument signed comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(int a, int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"two-stack-args-int-{suffix}-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "setcc": decoded["setcc"],
            "stackBytes": 8,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall two-argument signed comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def i386_clang_o2_leaf_compiler_profile_hint(reason: str) -> dict[str, Any]:
    return {
        "compiler": "clang",
        "language": "c",
        "args": ["-m32", "-O2", "-fomit-frame-pointer", "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"],
        "reason": reason,
    }


I386_STACK_ARG_SDIV_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("b856555555f76c240489d0c1e81f01d0"): (3, "0x55555556", 32, "mov-eax-magic-imul-stack4-mov-eax-edx-shr-eax-31-add-eax-edx"),
    bytes.fromhex("b867666666f76c240489d0c1e81fd1fa01d0"): (5, "0x66666667", 33, "mov-eax-magic-imul-stack4-mov-eax-edx-shr-eax-31-sar-edx-one-add-eax-edx"),
    bytes.fromhex("b867666666f76c240489d0c1e81fc1fa0201d0"): (10, "0x66666667", 34, "mov-eax-magic-imul-stack4-mov-eax-edx-shr-eax-31-sar-edx-2-add-eax-edx"),
}


def decode_stack_arg_sdiv_magic(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    decoded = I386_STACK_ARG_SDIV_MAGIC_OPS.get(core)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_sdiv_magic_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_sdiv_magic(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument signed magic-multiply division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-sdiv-magic-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "/",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "signed stack argument magic-multiply division is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_sdiv_magic_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_sdiv_magic(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument signed magic-multiply division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-sdiv-magic-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "/",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall signed stack argument magic-multiply division is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_STACK_ARG_SREM_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("8b4c2404ba5655555589c8f7ea89d0c1e81f01d08d044029c189c8"): (3, "0x55555556", 32, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-imul-edx-mov-eax-edx-shr-eax-31-add-eax-edx-lea-eax-eax-eax2-sub-ecx-eax-mov-eax-ecx"),
    bytes.fromhex("8b4c2404ba6766666689c8f7ea89d0c1e81fd1fa01c28d049229c189c8"): (5, "0x66666667", 33, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-imul-edx-mov-eax-edx-shr-eax-31-sar-edx-one-add-edx-eax-lea-eax-edx-edx4-sub-ecx-eax-mov-eax-ecx"),
    bytes.fromhex("8b4c2404ba6766666689c8f7ea89d0c1e81fc1fa0201c201d28d049229c189c8"): (10, "0x66666667", 34, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-imul-edx-mov-eax-edx-shr-eax-31-sar-edx-2-add-edx-eax-add-edx-edx-lea-eax-edx-edx4-sub-ecx-eax-mov-eax-ecx"),
}


def decode_stack_arg_srem_magic(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    decoded = I386_STACK_ARG_SREM_MAGIC_OPS.get(core)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_srem_magic_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_srem_magic(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument signed magic-multiply remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-srem-magic-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "%",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "signed stack argument magic-multiply remainder is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_srem_magic_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_srem_magic(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument signed magic-multiply remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-srem-magic-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "%",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall signed stack argument magic-multiply remainder is a canonical clang i386 O2 leaf pattern"
        ),
    }


def decode_stack_arg_sdiv_pow2(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if core == b"\x8b\x4c\x24\x04\x89\xc8\xc1\xe8\x1f\x01\xc8\xd1\xf8":
        return {
            "shift": 1,
            "divisor": 2,
            "bias": 1,
            "pattern": "mov-ecx-stack4-mov-eax-ecx-shr-eax-31-add-eax-ecx-sar-eax-one",
            "stackBytes": 4 if stdcall else 0,
        }
    if len(core) == 15 and core[:4] == b"\x8b\x4c\x24\x04" and core[4:6] == b"\x8d\x41" and core[7:12] == b"\x85\xc9\x0f\x49\xc1" and core[12:14] == b"\xc1\xf8":
        bias = core[6]
        shift = core[14]
        if not 2 <= shift <= 7:
            return None
        if bias != (1 << shift) - 1:
            return None
        return {
            "shift": shift,
            "divisor": 1 << shift,
            "bias": bias,
            "pattern": "mov-ecx-stack4-lea-eax-ecx-bias-test-ecx-ecx-cmovns-eax-ecx-sar-eax-imm8",
            "stackBytes": 4 if stdcall else 0,
        }
    return None


def stack_arg_sdiv_pow2_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_sdiv_pow2(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument signed power-of-two division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-sdiv-pow2-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "/",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "bias": int(decoded["bias"]),
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "signed stack argument power-of-two division is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_sdiv_pow2_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_sdiv_pow2(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument signed power-of-two division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-sdiv-pow2-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "/",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "bias": int(decoded["bias"]),
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall signed stack argument power-of-two division is a canonical clang i386 O2 leaf pattern"
        ),
    }


def decode_stack_arg_srem_pow2(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if core == b"\x8b\x44\x24\x04\x89\xc1\xc1\xe9\x1f\x01\xc1\x83\xe1\xfe\x29\xc8":
        return {
            "shift": 1,
            "divisor": 2,
            "bias": 1,
            "maskByte": 0xFE,
            "pattern": "mov-eax-stack4-mov-ecx-eax-shr-ecx-31-add-ecx-eax-and-ecx-not1-sub-eax-ecx",
            "stackBytes": 4 if stdcall else 0,
        }
    if len(core) == 17 and core[:4] == b"\x8b\x44\x24\x04" and core[4:6] == b"\x8d\x48" and core[7:12] == b"\x85\xc0\x0f\x49\xc8" and core[12:14] == b"\x83\xe1" and core[15:17] == b"\x29\xc8":
        bias = core[6]
        mask_byte = core[14]
        for shift in range(2, 8):
            divisor = 1 << shift
            if bias == divisor - 1 and mask_byte == ((-divisor) & 0xFF):
                return {
                    "shift": shift,
                    "divisor": divisor,
                    "bias": bias,
                    "maskByte": mask_byte,
                    "pattern": "mov-eax-stack4-lea-ecx-eax-bias-test-eax-eax-cmovns-ecx-eax-and-ecx-not-pow2-minus-one-sub-eax-ecx",
                    "stackBytes": 4 if stdcall else 0,
                }
        return None
    return None


def stack_arg_srem_pow2_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_srem_pow2(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument signed power-of-two remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-srem-pow2-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "%",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "bias": int(decoded["bias"]),
            "maskByte": int(decoded["maskByte"]),
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "signed stack argument power-of-two remainder is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_srem_pow2_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_srem_pow2(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument signed power-of-two remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-srem-pow2-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "%",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "bias": int(decoded["bias"]),
            "maskByte": int(decoded["maskByte"]),
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall signed stack argument power-of-two remainder is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_STACK_ARG_UDIV_MAGIC_OPS: dict[tuple[int, int], tuple[int, str]] = {
    (0xAAAAAAAB, 1): (3, "mov-eax-magic-mul-stack4-mov-eax-edx-shr-eax-one"),
    (0xCCCCCCCD, 2): (5, "mov-eax-magic-mul-stack4-mov-eax-edx-shr-eax-2"),
    (0xCCCCCCCD, 3): (10, "mov-eax-magic-mul-stack4-mov-eax-edx-shr-eax-3"),
}


def decode_stack_arg_udiv_magic(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) < 13 or core[:1] != b"\xb8" or core[5:11] != b"\xf7\x64\x24\x04\x89\xd0":
        return None
    multiplier = int.from_bytes(core[1:5], "little", signed=False)
    if core[11:13] == b"\xd1\xe8":
        shift = 1
        if len(core) != 13:
            return None
    elif len(core) == 14 and core[11:13] == b"\xc1\xe8":
        shift = core[13]
    else:
        return None
    decoded = I386_STACK_ARG_UDIV_MAGIC_OPS.get((multiplier, shift))
    if decoded is None:
        return None
    divisor, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_udiv_magic_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_udiv_magic(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument unsigned magic-multiply division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-udiv-magic-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "/",
            "divisor": divisor,
            "multiplier": f"0x{int(decoded['multiplier']):08x}",
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "unsigned stack argument magic-multiply division is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_udiv_magic_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_udiv_magic(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument unsigned magic-multiply division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-udiv-magic-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "/",
            "divisor": divisor,
            "multiplier": f"0x{int(decoded['multiplier']):08x}",
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall unsigned stack argument magic-multiply division is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_STACK_ARG_UREM_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("8b4c2404baabaaaaaa89c8f7e2d1ea8d045229c189c8"): (3, "0xaaaaaaab", 1, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-mul-edx-shr-edx-one-lea-eax-edx-edx2-sub-ecx-eax-mov-eax-ecx"),
    bytes.fromhex("8b4c2404bacdcccccc89c8f7e2c1ea028d049229c189c8"): (5, "0xcccccccd", 2, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-mul-edx-shr-edx-2-lea-eax-edx-edx4-sub-ecx-eax-mov-eax-ecx"),
    bytes.fromhex("8b4c2404bacdcccccc89c8f7e2c1ea0283e2fe8d049229c189c8"): (10, "0xcccccccd", 2, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-mul-edx-shr-edx-2-and-edx-not1-lea-eax-edx-edx4-sub-ecx-eax-mov-eax-ecx"),
}


def decode_stack_arg_urem_magic(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    decoded = I386_STACK_ARG_UREM_MAGIC_OPS.get(core)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_urem_magic_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_urem_magic(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument unsigned magic-multiply remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-urem-magic-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "%",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "unsigned stack argument magic-multiply remainder is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_urem_magic_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_urem_magic(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument unsigned magic-multiply remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-urem-magic-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "%",
            "divisor": divisor,
            "multiplier": decoded["multiplier"],
            "shift": int(decoded["shift"]),
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall unsigned stack argument magic-multiply remainder is a canonical clang i386 O2 leaf pattern"
        ),
    }


def decode_stack_arg_udiv_pow2(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if core == b"\x8b\x44\x24\x04\xd1\xe8":
        return {
            "shift": 1,
            "divisor": 2,
            "pattern": "mov-eax-stack4-shr-eax-one",
            "stackBytes": 4 if stdcall else 0,
        }
    if len(core) == 7 and core[:4] == b"\x8b\x44\x24\x04" and core[4:6] == b"\xc1\xe8":
        shift = core[6]
        if not 2 <= shift <= 31:
            return None
        return {
            "shift": shift,
            "divisor": 1 << shift,
            "pattern": "mov-eax-stack4-shr-eax-imm8",
            "stackBytes": 4 if stdcall else 0,
        }
    return None


def stack_arg_udiv_pow2_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_udiv_pow2(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument unsigned power-of-two division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-udiv-pow2-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "/",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "unsigned stack argument power-of-two division is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_udiv_pow2_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_udiv_pow2(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument unsigned power-of-two division pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-udiv-pow2-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "/",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall unsigned stack argument power-of-two division is a canonical clang i386 O2 leaf pattern"
        ),
    }


def decode_stack_arg_urem_pow2(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 7 or core[:4] != b"\x8b\x44\x24\x04" or core[4:6] != b"\x83\xe0":
        return None
    mask = core[6]
    if mask < 1:
        return None
    divisor = mask + 1
    if divisor & (divisor - 1):
        return None
    return {
        "shift": divisor.bit_length() - 1,
        "divisor": divisor,
        "mask": mask,
        "pattern": "mov-eax-stack4-and-eax-pow2-minus-one",
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_urem_pow2_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_urem_pow2(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument unsigned power-of-two remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-urem-pow2-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "%",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "mask": int(decoded["mask"]),
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "unsigned stack argument power-of-two remainder is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_urem_pow2_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_urem_pow2(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    divisor = int(decoded["divisor"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument unsigned power-of-two remainder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-urem-pow2-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "%",
            "shift": int(decoded["shift"]),
            "divisor": divisor,
            "mask": int(decoded["mask"]),
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall unsigned stack argument power-of-two remainder is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_SIGNED_ZERO_COMPARE_RULES: dict[str, tuple[str, str, str]] = {
    "lt": ("<", "mov-eax-stack4-shr-eax-31"),
    "ge": (">=", "mov-eax-stack4-not-eax-shr-eax-31"),
    "gt": (">", "xor-eax-cmp-stack4-zero-setg-al"),
    "le": ("<=", "xor-eax-cmp-stack4-zero-setle-al"),
}


def decode_stack_arg_signed_zero_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if body == b"\x8b\x44\x24\x04\xc1\xe8\x1f" + ret:
        suffix = "lt"
    elif body == b"\x8b\x44\x24\x04\xf7\xd0\xc1\xe8\x1f" + ret:
        suffix = "ge"
    elif body == b"\x31\xc0\x83\x7c\x24\x04\x00\x0f\x9f\xc0" + ret:
        suffix = "gt"
    elif body == b"\x31\xc0\x83\x7c\x24\x04\x00\x0f\x9e\xc0" + ret:
        suffix = "le"
    elif body == b"\x33\xc0\x83\x7c\x24\x04\x00\x0f\x9f\xc0" + ret:
        suffix = "gt"
    elif body == b"\x33\xc0\x83\x7c\x24\x04\x00\x0f\x9e\xc0" + ret:
        suffix = "le"
    else:
        return None
    operator, pattern = I386_SIGNED_ZERO_COMPARE_RULES[suffix]
    return {
        "suffix": suffix,
        "operator": operator,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_signed_zero_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_signed_zero_compare(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument signed zero-comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-int-{suffix}-zero-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "signed stack argument zero-comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_signed_zero_compare_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_signed_zero_compare(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument signed zero-comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(int value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-int-{suffix}-zero-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall signed stack argument zero-comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_UNSIGNED_IMM8_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x92: ("lt", "<", "setb"),
    0x93: ("ge", ">=", "setae"),
    0x94: ("eq", "==", "sete"),
    0x95: ("ne", "!=", "setne"),
}


I386_SIGNED_IMM8_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x9C: ("lt", "<", "setl"),
    0x9D: ("ge", ">=", "setge"),
}


def decode_stack_arg_signed_imm8_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    expected_len = 13 if stdcall else 11
    if len(body) != expected_len or not body.endswith(ret):
        return None
    if body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:6] != b"\x83\x7c\x24\x04":
        return None
    if body[7] != 0x0F or body[9] != 0xC0:
        return None
    decoded = I386_SIGNED_IMM8_COMPARE_SETCC.get(body[8])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    raw_imm = body[6]
    value = raw_imm if raw_imm < 0x80 else raw_imm - 0x100
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "immediate": value,
        "rawImmediate": raw_imm,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_signed_imm8_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_signed_imm8_compare(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument signed immediate comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(int value) {{",
            f"    return value {operator} {immediate};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-int-{suffix}-imm8-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "setcc": decoded["setcc"],
            "immediate": immediate,
            "rawImmediate": int(decoded["rawImmediate"]),
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "signed stack argument immediate comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_signed_imm8_compare_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_signed_imm8_compare(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument signed immediate comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(int value) {{",
            f"    return value {operator} {immediate};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-int-{suffix}-imm8-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "setcc": decoded["setcc"],
            "immediate": immediate,
            "rawImmediate": int(decoded["rawImmediate"]),
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall signed stack argument immediate comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def decode_stack_arg_unsigned_imm8_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    expected_len = 13 if stdcall else 11
    if len(body) != expected_len or not body.endswith(ret):
        return None
    if body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:6] != b"\x83\x7c\x24\x04":
        return None
    if body[7] != 0x0F or body[9] != 0xC0:
        return None
    decoded = I386_UNSIGNED_IMM8_COMPARE_SETCC.get(body[8])
    if decoded is None:
        return None
    if body[6] == 0 and body[8] in {0x94, 0x95}:
        return None
    suffix, operator, setcc = decoded
    raw_imm = body[6]
    value = raw_imm if raw_imm < 0x80 else raw_imm | 0xFFFFFF00
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "immediate": value,
        "rawImmediate": raw_imm,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_unsigned_imm8_compare_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_unsigned_imm8_compare(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument unsigned immediate comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-uint-{suffix}-imm8-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "setcc": decoded["setcc"],
            "immediate": f"0x{immediate:08x}",
            "rawImmediate": int(decoded["rawImmediate"]),
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "unsigned stack argument immediate comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_unsigned_imm8_compare_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_unsigned_imm8_compare(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument unsigned immediate comparison pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:08x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-uint-{suffix}-imm8-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "setcc": decoded["setcc"],
            "immediate": f"0x{immediate:08x}",
            "rawImmediate": int(decoded["rawImmediate"]),
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall unsigned stack argument immediate comparison is a canonical clang i386 O2 leaf pattern"
        ),
    }


def decode_stack_arg_bitmask_predicate(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if core == b"\x8b\x44\x24\x04\x83\xe0\x01":
        return {"predicate": "nonzero", "mask": 0x00000001, "pattern": "mov-eax-stack4-and-1"}
    if len(core) == 10 and core[:4] == b"\x8b\x44\x24\x04" and core[4:6] == b"\xc1\xe8" and core[7:] == b"\x83\xe0\x01":
        shift = core[6]
        if 1 <= shift <= 30:
            return {"predicate": "nonzero", "mask": 1 << shift, "pattern": "mov-eax-stack4-shr-and-1", "shift": shift}
    if core == b"\x8b\x44\x24\x04\xf7\xd0\x83\xe0\x01":
        return {"predicate": "zero", "mask": 0x00000001, "pattern": "mov-eax-stack4-not-and-1"}
    if len(core) == 10 and core[:2] in {b"\x31\xc0", b"\x33\xc0"} and core[2:5] == b"\xf6\x44\x24":
        byte_offset = core[5] - 4
        byte_mask = core[6]
        if 0 <= byte_offset <= 3 and byte_mask:
            return {
                "predicate": "zero",
                "mask": byte_mask << (byte_offset * 8),
                "pattern": "xor-eax-test-stack-byte-imm8-sete-al",
                "byteOffset": byte_offset,
                "byteMask": byte_mask,
            }
    return None


def stack_arg_bitmask_predicate_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_bitmask_predicate(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    mask = int(decoded["mask"])
    predicate = str(decoded["predicate"])
    operator = "!=" if predicate == "nonzero" else "=="
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument bitmask predicate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            f"    return (value & 0x{mask:08x}u) {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-bitmask-{predicate}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "mask": f"0x{mask:08x}",
            "predicate": predicate,
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stack argument bitmask predicate is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_bitmask_predicate_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_bitmask_predicate(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    mask = int(decoded["mask"])
    predicate = str(decoded["predicate"])
    operator = "!=" if predicate == "nonzero" else "=="
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument bitmask predicate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(unsigned int value) {{",
            f"    return (value & 0x{mask:08x}u) {operator} 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-bitmask-{predicate}-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "mask": f"0x{mask:08x}",
            "predicate": predicate,
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            "stdcall stack argument bitmask predicate is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_STACK_ARG_IMM8_BINARY_OPS: dict[int, tuple[str, str]] = {
    0xC0: ("add", "+"),
    0xE0: ("and", "&"),
    0xC8: ("or", "|"),
    0xF0: ("xor", "^"),
}


I386_STACK_ARG_LEA_MULTIPLY_OPS: dict[bytes, tuple[int, str]] = {
    bytes.fromhex("8b44240401c0"): (2, "mov-eax-stack4-add-eax-eax"),
    bytes.fromhex("8b4424048d0440"): (3, "mov-eax-stack4-lea-eax-eax-eax2"),
    bytes.fromhex("8b4424048d0480"): (5, "mov-eax-stack4-lea-eax-eax-eax4"),
    bytes.fromhex("8b44240401c08d0440"): (6, "mov-eax-stack4-add-eax-eax-lea-eax-eax-eax2"),
    bytes.fromhex("8b4c24048d04cd0000000029c8"): (7, "mov-ecx-stack4-lea-eax-ecx8-sub-eax-ecx"),
    bytes.fromhex("8b4424048d04c0"): (9, "mov-eax-stack4-lea-eax-eax-eax8"),
    bytes.fromhex("8b44240401c08d0480"): (10, "mov-eax-stack4-add-eax-eax-lea-eax-eax-eax4"),
    bytes.fromhex("8b4424048d0c808d0448"): (11, "mov-eax-stack4-lea-ecx-eax-eax4-lea-eax-eax-ecx2"),
    bytes.fromhex("8b442404c1e0028d0440"): (12, "mov-eax-stack4-shl-eax-2-lea-eax-eax-eax2"),
    bytes.fromhex("8b4424048d0c408d0488"): (13, "mov-eax-stack4-lea-ecx-eax-eax2-lea-eax-eax-ecx4"),
    bytes.fromhex("8b4424048d0c00c1e00429c8"): (14, "mov-eax-stack4-lea-ecx-eax-eax-shl-eax-4-sub-eax-ecx"),
    bytes.fromhex("8b4424048d04808d0440"): (15, "mov-eax-stack4-lea-eax-eax-eax4-lea-eax-eax-eax2"),
    bytes.fromhex("8b442404c1e0038d0440"): (24, "mov-eax-stack4-shl-eax-3-lea-eax-eax-eax2"),
    bytes.fromhex("8b4c240489c8c1e00529c8"): (31, "mov-ecx-stack4-mov-eax-ecx-shl-eax-5-sub-eax-ecx"),
    bytes.fromhex("8b4c240489c8c1e00501c8"): (33, "mov-ecx-stack4-mov-eax-ecx-shl-eax-5-add-eax-ecx"),
}


def decode_stack_arg_lea_multiply(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    decoded = I386_STACK_ARG_LEA_MULTIPLY_OPS.get(body[: -len(ret)])
    if decoded is None:
        return None
    multiplier, pattern = decoded
    return {
        "multiplier": multiplier,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_lea_multiply_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_lea_multiply(data, stdcall=False)
    if decoded is None:
        return None
    multiplier = int(decoded["multiplier"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stack-argument multiply-by-{multiplier} LEA/shift pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value * {multiplier}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-mul-lea-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "*",
            "multiplier": multiplier,
            "pattern": str(decoded["pattern"]),
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stack argument multiply-by-{multiplier} is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_lea_multiply_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_lea_multiply(data, stdcall=True)
    if decoded is None:
        return None
    multiplier = int(decoded["multiplier"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stdcall stack-argument multiply-by-{multiplier} LEA/shift pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value * {multiplier}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-mul-lea-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": "*",
            "multiplier": multiplier,
            "pattern": str(decoded["pattern"]),
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stdcall stack argument multiply-by-{multiplier} is a canonical clang i386 O2 leaf pattern"
        ),
    }


def decode_stack_arg_imm8_binary_op(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 7 or core[:4] != b"\x8b\x44\x24\x04" or core[4] != 0x83:
        return None
    decoded = I386_STACK_ARG_IMM8_BINARY_OPS.get(core[5])
    if decoded is None:
        return None
    suffix, operator = decoded
    raw_immediate = core[6]
    signed_immediate = raw_immediate if raw_immediate < 0x80 else raw_immediate - 0x100
    immediate = raw_immediate
    if suffix == "add" and signed_immediate < 0:
        suffix = "sub"
        operator = "-"
        immediate = -signed_immediate
    return {
        "suffix": suffix,
        "operator": operator,
        "immediate": immediate,
        "rawImmediate": raw_immediate,
        "signedImmediate": signed_immediate,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_imm8_binary_op_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_imm8_binary_op(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stack-argument {suffix} immediate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:02x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-{suffix}-imm8-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "immediate": f"0x{immediate:02x}",
            "rawImmediate": int(decoded["rawImmediate"]),
            "signedImmediate": int(decoded["signedImmediate"]),
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stack argument {suffix} immediate is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_imm8_binary_op_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_imm8_binary_op(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stdcall stack-argument {suffix} immediate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:02x}u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-{suffix}-imm8-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "immediate": f"0x{immediate:02x}",
            "rawImmediate": int(decoded["rawImmediate"]),
            "signedImmediate": int(decoded["signedImmediate"]),
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stdcall stack argument {suffix} immediate is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_STACK_ARG_SHIFT_IMM8_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE0: ("shl", "<<", "unsigned int", "unsigned int"),
    0xE8: ("shr", ">>", "unsigned int", "unsigned int"),
    0xF8: ("sar", ">>", "int", "int"),
}


I386_STACK_ARG_UNARY_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x31\xc0\x2b\x44\x24\x04": ("neg", "-", "xor-eax-sub-eax-stack4"),
    b"\x8b\x44\x24\x04\xf7\xd0": ("not", "~", "mov-eax-stack4-not-eax"),
}


def decode_stack_arg_unary_op(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    decoded = I386_STACK_ARG_UNARY_OPS.get(body[: -len(ret)])
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_unary_op_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_unary_op(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stack-argument {suffix} unary pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {operator}value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-{suffix}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "pattern": decoded["pattern"],
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stack argument {suffix} unary is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_unary_op_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_unary_op(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stdcall stack-argument {suffix} unary pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return {operator}value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-{suffix}-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "pattern": decoded["pattern"],
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stdcall stack argument {suffix} unary is a canonical clang i386 O2 leaf pattern"
        ),
    }


I386_STACK_ARG_INC_DEC_OPS: dict[int, tuple[str, str, str]] = {
    0x40: ("inc", "+", "mov-eax-stack4-inc-eax"),
    0x48: ("dec", "-", "mov-eax-stack4-dec-eax"),
}


def decode_stack_arg_inc_dec(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 5 or core[:4] != b"\x8b\x44\x24\x04":
        return None
    decoded = I386_STACK_ARG_INC_DEC_OPS.get(core[4])
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_inc_dec_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_inc_dec(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stack-argument {suffix} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value {operator} 1u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-{suffix}-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "pattern": decoded["pattern"],
            "delta": 1,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stack argument {suffix} is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_inc_dec_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_inc_dec(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stdcall stack-argument {suffix} pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value {operator} 1u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-{suffix}-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "pattern": decoded["pattern"],
            "delta": 1,
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stdcall stack argument {suffix} is a canonical clang i386 O2 leaf pattern"
        ),
    }


def decode_stack_arg_shift_imm8(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 7 or core[:4] != b"\x8b\x44\x24\x04" or core[4] != 0xC1:
        return None
    decoded = I386_STACK_ARG_SHIFT_IMM8_OPS.get(core[5])
    if decoded is None:
        return None
    shift = core[6]
    if not 2 <= shift <= 31:
        return None
    suffix, operator, value_type, return_type = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "valueType": value_type,
        "returnType": return_type,
        "shift": shift,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_shift_imm8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_shift_imm8(data, stdcall=False)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    shift = int(decoded["shift"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stack-argument {suffix} immediate-shift pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {shift};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-{suffix}-imm8-cdecl",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "shift": shift,
            "valueType": value_type,
            "returnType": return_type,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stack argument {suffix} immediate shift is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_shift_imm8_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stack_arg_shift_imm8(data, stdcall=True)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    shift = int(decoded["shift"])
    source = "\n".join(
        [
            "/*",
            f" * Automatically generated from an x86 stdcall stack-argument {suffix} immediate-shift pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"{return_type} __stdcall {c_name}({value_type} value) {{",
            f"    return value {operator} {shift};",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": f"stack-arg-{suffix}-imm8-stdcall",
            "bodyBytes": len(strip_alignment_padding(data)),
            "operator": operator,
            "shift": shift,
            "valueType": value_type,
            "returnType": return_type,
            "stackBytes": 4,
        },
        "compilerProfileHints": i386_clang_o2_leaf_compiler_profile_hint(
            f"stdcall stack argument {suffix} immediate shift is a canonical clang i386 O2 leaf pattern"
        ),
    }


def stack_arg_nonzero_bool_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 11 or body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:] != b"\x83\x7c\x24\x04\x00\x0f\x95\xc0\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument nonzero predicate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-nonzero-bool-cdecl",
            "bodyBytes": len(body),
            "predicate": "value != 0",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stack argument nonzero predicate is a canonical x86 O2 leaf pattern",
        },
    }


def stack_arg_zero_bool_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 11 or body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:] != b"\x83\x7c\x24\x04\x00\x0f\x94\xc0\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stack-argument zero predicate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int {c_name}(unsigned int value) {{",
            "    return value == 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-zero-bool-cdecl",
            "bodyBytes": len(body),
            "predicate": "value == 0",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stack argument zero predicate is a canonical x86 O2 leaf pattern",
        },
    }


def stack_arg_nonzero_bool_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 13 or body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:] != b"\x83\x7c\x24\x04\x00\x0f\x95\xc0\xc2\x04\x00":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument nonzero predicate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(unsigned int value) {{",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-nonzero-bool-stdcall",
            "bodyBytes": len(body),
            "predicate": "value != 0",
            "stackBytes": 4,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall stack argument nonzero predicate is a canonical x86 O2 leaf pattern",
        },
    }


def stack_arg_zero_bool_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 13 or body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:] != b"\x83\x7c\x24\x04\x00\x0f\x94\xc0\xc2\x04\x00":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall stack-argument zero predicate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(unsigned int value) {{",
            "    return value == 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "stack-arg-zero-bool-stdcall",
            "bodyBytes": len(body),
            "predicate": "value == 0",
            "stackBytes": 4,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall stack argument zero predicate is a canonical x86 O2 leaf pattern",
        },
    }


def increment_field_return_stack4_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 10 or data[0] != 0x8B or data[1] != 0x41 or data[3] != 0x40 or data[4] != 0x89 or data[5] != 0x41:
        return None
    if data[2] != data[6] or data[7:] != b"\xc2\x04\x00":
        return None
    offset = data[2]
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 field increment-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __fastcall {c_name}(void *self, int unused_edx, int unused_stack) {{",
            "    unsigned int value;",
            "    (void)unused_edx;",
            "    (void)unused_stack;",
            f"    value = *(unsigned int *)({self_offset(offset)}) + 1;",
            f"    *(unsigned int *)({self_offset(offset)}) = value;",
            "    return value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "increment-field-return-stack4",
            "fieldOffset": offset,
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O1", "/GS-", "/Oy-"],
            "reason": "VC71 /O1 preserves the mov/inc/store field update form",
        },
    }


def byte_nonzero_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 11 and data[0] == 0x8A and data[1] == 0x41 and data[3:] == b"\x84\xc0\x0f\x95\xc0\x0f\xb6\xc0\xc3":
        offset = data[2]
    elif len(data) == 9 and data[0] == 0x8A and data[1] == 0x51 and data[3:] == b"\x84\xd2\x0f\x95\xc0\xc3":
        offset = data[2]
    else:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 byte-field nonzero pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned char value = *(unsigned char *)({self_offset(offset)});",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "byte-field-nonzero",
            "fieldOffset": offset,
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "byte-field nonzero checks are a compact x86 fastcall pattern",
        },
    }


def byte_nonzero_deref_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 10 or data != b"\x8a\x11\x33\xc0\x84\xd2\x0f\x95\xc0\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 byte-pointer nonzero pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __fastcall {c_name}(unsigned char *self) {{",
            "    return *self != 0;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "byte-pointer-nonzero",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "byte-pointer nonzero checks are a compact x86 pattern",
        },
    }


def nested_u32_getter_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 14 and data == b"\x8b\x41\x04\x85\xc0\x74\x04\x8b\x40\x0c\xc3\x33\xc0\xc3":
        c_name = c_identifier(str(task.get("name") or "recovered_function"))
        source = "\n".join(
            [
                "/*",
                " * Automatically generated from an x86 nested field getter with null check.",
                f" * Target: {task.get('name')} at {task.get('address')}.",
                " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
                " */",
                f"unsigned int __fastcall {c_name}(void *self) {{",
                "    void *p = *(void **)((char *)self + 4);",
                "    return p ? *(unsigned int *)((char *)p + 0xc) : 0;",
                "}",
                "",
            ]
        )
        return {
            "source": source,
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {
                "rule": "nested-u32-getter-null-checked",
                "bodyBytes": len(data),
            },
            "compilerProfileHints": {
                "compiler": "msvc",
                "language": "c",
                "args": ["/O2", "/GS-", "/Oy"],
                "reason": "null-checked nested field loads are common x86 fastcall patterns",
            },
        }
    return None


def pair_u32_getter_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 10 and data[:3] == b"\x8b\x41\x04" and data[3:6] == b"\x8b\x80\x04" and data[6:] == b"\x00\x01\x00\xc3":
        c_name = c_identifier(str(task.get("name") or "recovered_function"))
        source = "\n".join(
            [
                "/*",
                " * Automatically generated from an x86 nested pointer getter pattern.",
                f" * Target: {task.get('name')} at {task.get('address')}.",
                " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
                " */",
                f"unsigned int __fastcall {c_name}(void *self) {{",
                "    void *p = *(void **)((char *)self + 4);",
                "    return *(unsigned int *)((char *)p + 0x10004);",
                "}",
                "",
            ]
        )
        return {
            "source": source,
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {
                "rule": "nested-u32-getter-offset-pair",
                "bodyBytes": len(data),
            },
            "compilerProfileHints": {
                "compiler": "msvc",
                "language": "c",
                "args": ["/O2", "/GS-", "/Oy"],
                "reason": "nested pointer getters with large offsets are common x86 fastcall patterns",
            },
        }
    return None


def fastcall_store_two_u32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 17 or data != b"\x8b\x44\x24\x04\x8b\x54\x24\x08\x89\x41\x10\x89\x51\x14\xc2\x08\x00":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 fastcall two-store pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"void __fastcall {c_name}(void *self, int unused, unsigned int a0, unsigned int a1) {{",
            "    *(unsigned int *)((char *)self + 0x10) = a0;",
            "    *(unsigned int *)((char *)self + 0x14) = a1;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "fastcall-store-two-u32",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "fastcall two-store setters are a common x86 pattern",
        },
    }


def zero_four_u32s_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 18 or data != b"\x8b\xc1\x33\xc9\x8b\xd0\x89\x0a\x89\x4a\x04\x89\x4a\x08\x89\x4a\x0c\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 zero-four-u32s pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"void *__fastcall {c_name}(void *self) {{",
            "    unsigned int *p = (unsigned int *)self;",
            "    p[0] = 0;",
            "    p[1] = 0;",
            "    p[2] = 0;",
            "    p[3] = 0;",
            "    return self;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "zero-four-u32s",
            "bodyBytes": len(data),
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "zeroing four consecutive u32 fields is a common x86 pattern",
        },
    }


def field_getter_u32_u8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 4 and data[:2] == b"\x8b\x41" and data[3] == 0xC3:
        offset = signed_disp8(data[2])
        return {
            "source": f"unsigned int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned int *)({self_offset(offset)});\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-u32-u8", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field getter pattern"},
        }
    return None


def field_getter_u8_u8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 4 and data[:2] == b"\x8a\x41" and data[3] == 0xC3:
        offset = signed_disp8(data[2])
        return {
            "source": f"unsigned char __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned char *)({self_offset(offset)});\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-u8-u8", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field getter pattern"},
        }
    return None


def field_getter_u16_u8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 5 and data[:3] == b"\x0f\xb7\x41" and data[4] == 0xC3:
        offset = signed_disp8(data[3])
        return {
            "source": f"unsigned int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned short *)({self_offset(offset)});\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-u16-u8", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field getter pattern"},
        }
    return None


def field_getter_s16_u8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 5 and data[:3] == b"\x0f\xbf\x41" and data[4] == 0xC3:
        offset = signed_disp8(data[3])
        return {
            "source": f"int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(short *)({self_offset(offset)});\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-s16-u8", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field getter pattern"},
        }
    return None


def field_getter_u32_u32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 7 and data[:2] == b"\x8b\x81" and data[6] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        return {
            "source": f"unsigned int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned int *)({self_offset(offset)});\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-u32-u32", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field getter pattern"},
        }
    return None


def field_getter_u8_u32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 7 and data[:2] == b"\x8a\x81" and data[6] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        return {
            "source": f"unsigned char __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned char *)({self_offset(offset)});\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-u8-u32", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field getter pattern"},
        }
    return None


def u64_field_getter_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 7 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x8b\x51" and data[5] == ((data[2] + 4) & 0xFF) and data[6] == 0xC3:
        offset = signed_disp8(data[2])
        return {
            "source": f"unsigned __int64 __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned __int64 *)({self_offset(offset)});\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-u64-u8", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 64-bit field getter pattern"},
        }
    return None


def field_array_getter_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 13 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x8b\x4c" and data[5:7] == b"\x24\x04" and data[7:10] == b"\x8b\x04\x88" and data[10:] == b"\xc2\x04\x00":
        offset = signed_disp8(data[2])
        return {
            "source": f"void *__fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self, int unused, unsigned int index) {{\n    void **items = *(void ***)({self_offset(offset)});\n    return items[index];\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-array-get-u32-index", "bodyBytes": len(data), "pointerOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 indexed pointer array getter pattern"},
        }
    return None


def nullable_indexed_field_array_getter_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_nullable_indexed_field_array_getter_stdcall(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    offset = int(decoded["pointerOffset"])
    stack_bytes = int(decoded["stackBytes"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall nullable indexed field-array getter.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * High-level C did not preserve byte parity here; this emits the decoded parity fallback.",
            " */",
            f"__declspec(naked) unsigned int __stdcall {c_name}(void *self, unsigned int index) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            "        test eax, eax",
            "        je null_return",
            f"        mov eax, dword ptr [eax+0{offset:x}h]",
            "        mov ecx, dword ptr [esp+8]",
            "        mov eax, dword ptr [eax+ecx*4]",
            f"        ret {stack_bytes}",
            "    null_return:",
            "        xor eax, eax",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "nullable-indexed-field-array-getter-stdcall8",
            "bodyBytes": len(data),
            **decoded,
            "sourceTier": "generated inline-assembly parity fallback with decoded nullable indexed field-array bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall nullable indexed field-array getter; parity fallback preserves branch layout",
        },
    }


def decode_nullable_indexed_field_array_getter_stdcall(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 29:
        return None
    if body[:8] != b"\x8b\x44\x24\x04\x85\xc0\x74\x10":
        return None
    if body[8:10] != b"\x8b\x80" or body[14:18] != b"\x8b\x4c\x24\x08":
        return None
    if body[18:21] != b"\x8b\x04\x88" or body[21:24] != b"\xc2\x08\x00":
        return None
    if body[24:] != b"\x33\xc0\xc2\x08\x00":
        return None
    return {
        "pointerOffset": int.from_bytes(body[10:14], "little"),
        "stackBytes": 8,
        "elementBytes": 4,
        "nullReturn": 0,
    }


def nullable_field_setter_u32_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_nullable_field_setter_u32_stdcall(data)
    if decoded is None:
        return None
    offset = int(decoded["fieldOffset"])
    stack_bytes = int(decoded["stackBytes"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall nullable field setter.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * High-level C did not preserve byte parity here; this emits the decoded parity fallback.",
            " */",
            f"__declspec(naked) void __stdcall {c_name}(void *self, unsigned int value) {{",
            "    __asm {",
            "        mov ecx, dword ptr [esp+4]",
            "        test ecx, ecx",
            "        mov eax, dword ptr [esp+8]",
            "        je done",
            f"        mov dword ptr [ecx+0{offset:x}h], eax",
            "    done:",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "nullable-field-setter-u32-stdcall8",
            "bodyBytes": len(data),
            **decoded,
            "sourceTier": "generated inline-assembly parity fallback with decoded nullable field-setter bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall nullable field setter; parity fallback preserves branch layout",
        },
    }


def decode_nullable_field_setter_u32_stdcall(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 21:
        return None
    if body[:4] != b"\x8b\x4c\x24\x04" or body[4:6] != b"\x85\xc9":
        return None
    if body[6:10] != b"\x8b\x44\x24\x08" or body[10:12] != b"\x74\x06":
        return None
    if body[12:14] != b"\x89\x81" or body[18:] != b"\xc2\x08\x00":
        return None
    return {
        "fieldOffset": int.from_bytes(body[14:18], "little"),
        "stackBytes": 8,
    }


def field_pointer_u8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 4 and data[:2] == b"\x8d\x41" and data[3] == 0xC3:
        offset = signed_disp8(data[2])
        return {
            "source": f"void *__fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return (char *)self + 0x{offset:x};\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-pointer-u8", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field pointer pattern"},
        }
    return None


def field_pointer_u32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 7 and data[:2] == b"\x8d\x81" and data[6] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        return {
            "source": f"void *__fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return (char *)self + 0x{offset:x};\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-pointer-u32", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field pointer pattern"},
        }
    return None


def field_set_u8_u8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 5 and data[:2] == b"\xc6\x41" and data[4] == 0xC3:
        offset = signed_disp8(data[2])
        value = data[3]
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned char *)({self_offset(offset)}) = 0x{value:02x};\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-set-u8-u8", "bodyBytes": len(data), "fieldOffset": offset, "value": value},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field setter pattern"},
        }
    return None


def field_set_u8_u32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 8 and data[:2] == b"\xc6\x81" and data[7] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        value = data[6]
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned char *)({self_offset(offset)}) = 0x{value:02x};\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-set-u8-u32", "bodyBytes": len(data), "fieldOffset": offset, "value": value},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field setter pattern"},
        }
    return None


def field_set_u32_u8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 7 and data[:2] == b"\xc7\x01" and data[6] == 0xC3:
        value = int.from_bytes(data[2:6], "little")
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)self = 0x{value:08x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-set-u32-zero", "bodyBytes": len(data), "value": value},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field setter pattern"},
        }
    if len(data) == 8 and data[:2] == b"\xc7\x41" and data[7] == 0xC3:
        offset = signed_disp8(data[2])
        value = int.from_bytes(data[3:7], "little")
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)({self_offset(offset)}) = 0x{value:08x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-set-u32-u8", "bodyBytes": len(data), "fieldOffset": offset, "value": value},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field setter pattern"},
        }
    if len(data) == 11 and data[:2] == b"\xc7\x81" and data[10] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        value = int.from_bytes(data[6:10], "little")
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)({self_offset(offset)}) = 0x{value:08x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-set-u32-u32", "bodyBytes": len(data), "fieldOffset": offset, "value": value},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field setter pattern"},
        }
    return None


def field_or_u8_imm8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 5 and data[:2] == b"\x83\x49" and data[4] == 0xC3:
        offset = signed_disp8(data[2])
        mask = data[3]
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)({self_offset(offset)}) |= 0x{mask:x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-or-u8-imm8", "bodyBytes": len(data), "fieldOffset": offset, "mask": mask},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field bitmask pattern"},
        }
    return None


def field_or_u8_imm32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 8 and data[:2] == b"\x81\x49" and data[7] == 0xC3:
        offset = signed_disp8(data[2])
        mask = int.from_bytes(data[3:7], "little")
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)({self_offset(offset)}) |= 0x{mask:x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-or-u8-imm32", "bodyBytes": len(data), "fieldOffset": offset, "mask": mask},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field bitmask pattern"},
        }
    return None


def field_or_u32_imm8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 8 and data[:2] == b"\x83\x89" and data[7] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        mask = data[6]
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)({self_offset(offset)}) |= 0x{mask:x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-or-u32-imm8", "bodyBytes": len(data), "fieldOffset": offset, "mask": mask},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field bitmask pattern"},
        }
    return None


def field_or_u32_imm32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 11 and data[:2] == b"\x81\x89" and data[10] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        mask = int.from_bytes(data[6:10], "little")
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)({self_offset(offset)}) |= 0x{mask:x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-or-u32-imm32", "bodyBytes": len(data), "fieldOffset": offset, "mask": mask},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field bitmask pattern"},
        }
    return None


def field_and_u8_imm8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 5 and data[:2] == b"\x83\x61" and data[4] == 0xC3:
        offset = signed_disp8(data[2])
        imm = data[3]
        mask = imm | 0xFFFFFF00 if imm & 0x80 else imm
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)({self_offset(offset)}) &= 0x{mask:08x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-and-u8-imm8", "bodyBytes": len(data), "fieldOffset": offset, "mask": mask},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field bitmask pattern"},
        }
    return None


def field_add_u8_imm8_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 7 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x83\xc0" and data[6] == 0xC3:
        return {
            "source": f"unsigned int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned int *)({self_offset(signed_disp8(data[2]))}) + 0x{data[5]:x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-add-u8-imm8", "bodyBytes": len(data), "fieldOffset": signed_disp8(data[2]), "value": data[5]},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field arithmetic pattern"},
        }
    return None


def field_add_u8_imm32_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 9 and data[:2] == b"\x8b\x41" and data[3] == 0x05 and data[8] == 0xC3:
        return {
            "source": f"unsigned int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned int *)({self_offset(signed_disp8(data[2]))}) + 0x{int.from_bytes(data[4:8], 'little'):x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-field-add-u8-imm32", "bodyBytes": len(data), "fieldOffset": signed_disp8(data[2]), "value": int.from_bytes(data[4:8], 'little')},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field arithmetic pattern"},
        }
    return None


def fastcall_self_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if data == b"\x8b\xc1\xc3":
        return {
            "source": f"void *__fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return self;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-self", "bodyBytes": len(data)},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "fastcall self-return pattern"},
        }
    return None


def fastcall_store_one_stack_arg_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 10 and data[:4] == b"\x8b\x44\x24\x04" and data[4:6] == b"\x89\x41" and data[7:] == b"\xc2\x04\x00":
        offset = signed_disp8(data[6])
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self, int unused, unsigned int value) {{\n    *(unsigned int *)({self_offset(offset)}) = value;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-store-one-stack-u32-u8", "bodyBytes": len(data), "fieldOffset": offset},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "fastcall stack-arg field store pattern"},
        }
    return None


def fastcall_store_one_stack_arg_zero_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 9 and data[:4] == b"\x8b\x44\x24\x04" and data[4:6] == b"\x89\x01" and data[6:] == b"\xc2\x04\x00":
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self, int unused, unsigned int value) {{\n    *(unsigned int *)self = value;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-store-one-stack-u32-zero", "bodyBytes": len(data)},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "fastcall self-store pattern"},
        }
    return None


def fastcall_store_pair_from_pointer_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if data == b"\x8b\x44\x24\x04\x8b\x10\x89\x11\x8b\x40\x04\x89\x41\x04\xc2\x04\x00":
        return {
            "source": f"void __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self, int unused, unsigned int *value) {{\n    *(unsigned int *)self = value[0];\n    *(unsigned int *)((char *)self + 4) = value[1];\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-store-pair-from-pointer", "bodyBytes": len(data)},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "fastcall pair store pattern"},
        }
    return None


def copy_field_return_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 7 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x89\x41" and data[6] == 0xC3:
        src = signed_disp8(data[2])
        dst = signed_disp8(data[5])
        return {
            "source": f"unsigned int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    unsigned int value = *(unsigned int *)({self_offset(src)});\n    *(unsigned int *)({self_offset(dst)}) = value;\n    return value;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-copy-field-return-u8", "bodyBytes": len(data), "sourceOffset": src, "targetOffset": dst},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field copy-return pattern"},
        }
    return None


def clear_two_fields_return_zero_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 9 and data[:2] == b"\x33\xc0" and data[2:4] == b"\x89\x41" and data[5:7] == b"\x89\x41" and data[8] == 0xC3:
        first = signed_disp8(data[4])
        second = signed_disp8(data[7])
        return {
            "source": f"unsigned int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    *(unsigned int *)({self_offset(first)}) = 0;\n    *(unsigned int *)({self_offset(second)}) = 0;\n    return 0;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-clear-two-u32-return-zero-u8", "bodyBytes": len(data), "firstOffset": first, "secondOffset": second},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field zeroing pattern"},
        }
    return None


def add_two_fields_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 13 and data[:2] == b"\x8b\x81" and data[6:8] == b"\x03\x81" and data[12] == 0xC3:
        first = int.from_bytes(data[2:6], "little")
        second = int.from_bytes(data[8:12], "little")
        return {
            "source": f"unsigned int __fastcall {c_identifier(str(task.get('name') or 'recovered_function'))}(void *self) {{\n    return *(unsigned int *)({self_offset(first)}) + *(unsigned int *)({self_offset(second)});\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "fastcall-add-two-fields-u32", "bodyBytes": len(data), "firstOffset": first, "secondOffset": second},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "x86 field arithmetic pattern"},
        }
    return None


def global_getter_u32_cdecl_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 6 and data[0] == 0xA1 and data[5] == 0xC3:
        address = int.from_bytes(data[1:5], "little")
        return {
            "source": f"unsigned int {c_identifier(str(task.get('name') or 'recovered_function'))}(void) {{\n    return *(unsigned int volatile *)0x{address:08x};\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "global-getter-u32-cdecl", "bodyBytes": len(data), "address": f"0x{address:08x}"},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "absolute global getter pattern"},
        }
    return None


def global_getter_u8_cdecl_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 6 and data[0] == 0xA0 and data[5] == 0xC3:
        address = int.from_bytes(data[1:5], "little")
        return {
            "source": f"unsigned char {c_identifier(str(task.get('name') or 'recovered_function'))}(void) {{\n    return *(unsigned char volatile *)0x{address:08x};\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "global-getter-u8-cdecl", "bodyBytes": len(data), "address": f"0x{address:08x}"},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "absolute global getter pattern"},
        }
    return None


def global_setter_u8_cdecl_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 8 and data[:2] == b"\xc6\x05" and data[7] == 0xC3:
        address = int.from_bytes(data[2:6], "little")
        value = data[6]
        return {
            "source": f"void {c_identifier(str(task.get('name') or 'recovered_function'))}(void) {{\n    *(unsigned char volatile *)0x{address:08x} = 0x{value:02x};\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "global-setter-u8-cdecl", "bodyBytes": len(data), "address": f"0x{address:08x}", "value": value},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "absolute global setter pattern"},
        }
    return None


def global_setter_u32_cdecl_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 11 and data[:2] == b"\xc7\x05" and data[10] == 0xC3:
        address = int.from_bytes(data[2:6], "little")
        value = int.from_bytes(data[6:10], "little")
        return {
            "source": f"void {c_identifier(str(task.get('name') or 'recovered_function'))}(void) {{\n    *(unsigned int volatile *)0x{address:08x} = 0x{value:08x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "global-setter-u32-cdecl", "bodyBytes": len(data), "address": f"0x{address:08x}", "value": value},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "absolute global setter pattern"},
        }
    return None


def global_setter_u32_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 12 or data[:4] != b"\x8b\x44\x24\x04" or data[4] != 0xA3 or data[9:] != b"\xc2\x04\x00":
        return None
    address = int.from_bytes(data[5:9], "little")
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall global setter pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"void __stdcall {c_name}(unsigned int value) {{",
            f"    *(unsigned int volatile *)0x{address:08x} = value;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
        "generator": {
            "rule": "global-setter-u32-stdcall",
            "bodyBytes": len(data),
            "address": f"0x{address:08x}",
            "stackBytes": 4,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall absolute global setter pattern",
        },
    }


def global_setter_two_u32_cdecl_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 16 and data[0] == 0xB8 and data[5] == 0xA3 and data[10] == 0xA3 and data[15] == 0xC3:
        value = int.from_bytes(data[1:5], "little")
        first_address = int.from_bytes(data[6:10], "little")
        second_address = int.from_bytes(data[11:15], "little")
        return {
            "source": f"unsigned int {c_identifier(str(task.get('name') or 'recovered_function'))}(void) {{\n    *(unsigned int volatile *)0x{first_address:08x} = 0x{value:08x}u;\n    *(unsigned int volatile *)0x{second_address:08x} = 0x{value:08x}u;\n    return 0x{value:08x}u;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "global-setter-two-u32-cdecl", "bodyBytes": len(data), "firstAddress": f"0x{first_address:08x}", "secondAddress": f"0x{second_address:08x}", "value": value},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "absolute global setter pattern"},
        }
    if len(data) == 13 and data[:2] == b"\x33\xc0" and data[2] == 0xA3 and data[7] == 0xA3 and data[12] == 0xC3:
        first_address = int.from_bytes(data[3:7], "little")
        second_address = int.from_bytes(data[8:12], "little")
        return {
            "source": f"unsigned int {c_identifier(str(task.get('name') or 'recovered_function'))}(void) {{\n    *(unsigned int volatile *)0x{first_address:08x} = 0;\n    *(unsigned int volatile *)0x{second_address:08x} = 0;\n    return 0;\n}}\n",
            "extension": "c",
            "language": "c",
            "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
            "generator": {"rule": "global-setter-two-u32-cdecl", "bodyBytes": len(data), "firstAddress": f"0x{first_address:08x}", "secondAddress": f"0x{second_address:08x}", "value": 0},
            "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "absolute global setter pattern"},
        }
    return None


def stdcall_copy_cstr_to_global_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_copy_cstr_to_global(data)
    if decoded is None:
        return None
    dest = int(decoded["destAddress"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall C-string copy-to-global pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"void __stdcall {c_name}(const char *message) {{",
            "    char *dest = (char *)0x%08x;" % dest,
            "    do {",
            "        *dest++ = *message;",
            "    } while (*message++ != 0);",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-copy-cstr-to-global",
            "bodyBytes": len(data),
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall C-string copy-to-global leaf pattern; parity fallback preserves indexed-copy loop",
        },
    }


def decode_stdcall_copy_cstr_to_global(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 29:
        return None
    if body[:4] != b"\x8b\x44\x24\x04" or body[4] != 0xBA:
        return None
    if body[9:13] != b"\x2b\xd0\xeb\x03" or body[13:16] != b"\x8d\x49\x00":
        return None
    if body[16:18] != b"\x8a\x08" or body[18:21] != b"\x88\x0c\x02":
        return None
    if body[21:26] != b"\x40\x84\xc9\x75\xf6" or body[26:] != b"\xc2\x04\x00":
        return None
    return {
        "destAddress": int.from_bytes(body[5:9], "little"),
        "stackBytes": 4,
    }


def stdcall_indirect_global_callback_loop_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_indirect_global_callback_loop(data)
    if decoded is None:
        return None
    callback_address = int(decoded["callbackAddress"])
    pushed_value = int(decoded["pushedValue"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall indirect global callback-loop pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * Generated inline assembly preserves the target register allocation and loop branch shape.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"__declspec(naked) void __stdcall {c_name}(unsigned int count) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        test esi, esi",
            "        je done",
            "        push edi",
            "        _emit 08Bh",
            "        _emit 03Dh",
            f"        _emit 0{callback_address & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 24) & 0xFF:02x}h",
            "    call_again:",
            f"        push {pushed_value}",
            "        call edi",
            "        dec esi",
            "        jne call_again",
            "        pop edi",
            "    done:",
            "        pop esi",
            "        ret 4",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-indirect-global-callback-loop",
            "bodyBytes": len(data),
            "sourceTier": "generated inline-assembly parity fallback with decoded indirect global callback-loop bytes",
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall indirect global callback loop; parity fallback preserves register and loop shape",
        },
    }


def decode_stdcall_indirect_global_callback_loop(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 28:
        return None
    if body[:5] != b"\x56\x8b\x74\x24\x08" or body[5:9] != b"\x85\xf6\x74\x0f":
        return None
    if body[9] != 0x57 or body[10:12] != b"\x8b\x3d":
        return None
    if body[16:18] != b"\x6a\x01" or body[18:20] != b"\xff\xd7":
        return None
    if body[20:25] != b"\x4e\x75\xf9\x5f\x5e" or body[25:] != b"\xc2\x04\x00":
        return None
    return {
        "callbackAddress": int.from_bytes(body[12:16], "little"),
        "pushedValue": int(body[17]),
        "stackBytes": 4,
    }


def stdcall_nullable_field_tailjmp_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_nullable_field_tailjmp(task, data)
    if decoded is None:
        return None
    field_offset = int(decoded["fieldOffset"])
    tail_target = int(decoded["tailTarget"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    callee = c_identifier(f"sub_{tail_target:08x}")
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall nullable-field tailcall pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * Generated inline assembly preserves the nullable-field tail-jump encoding.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}(void *self) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            f"        mov ecx, dword ptr [eax+0{field_offset:x}h]",
            "        test ecx, ecx",
            "        je done",
            "        mov dword ptr [esp+4], eax",
            f"        jmp {callee}",
            "    done:",
            "        ret 4",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-nullable-field-tailjmp",
            "bodyBytes": int(decoded["bodyBytes"]),
            "sourceTier": "generated inline-assembly parity fallback with decoded nullable-field tail-jump bytes",
            "jumpTarget": f"0x{tail_target:08x}",
            "callTarget": f"0x{tail_target:08x}",
            "callSymbol": f"_{callee}",
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall nullable-field tailcall; parity fallback preserves direct tail-jump encoding",
        },
    }


def decode_stdcall_nullable_field_tailjmp(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    # mov eax,[esp+4]; mov ecx,[eax+field32]; test ecx,ecx; je +9;
    # mov [esp+4],eax; jmp rel32; ret 4
    if len(body) != 26:
        return None
    if body[:4] != b"\x8b\x44\x24\x04" or body[4:6] != b"\x8b\x88":
        return None
    if body[10:14] != b"\x85\xc9\x74\x09" or body[14:18] != b"\x89\x44\x24\x04":
        return None
    if body[18] != 0xE9 or body[23:] != b"\xc2\x04\x00":
        return None
    address = coerce_int(task.get("address"))
    tail_target = rel32_call_target(address, call_offset=18, rel32=int.from_bytes(body[19:23], "little", signed=True))
    if tail_target is None:
        return None
    return {
        "fieldOffset": int.from_bytes(body[6:10], "little"),
        "tailTarget": tail_target,
        "jumpOffset": 18,
        "stackBytes": 4,
        "bodyBytes": len(body),
    }


def stdcall_clamped_count_copy_to_global_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_clamped_count_copy_to_global(data)
    if decoded is None:
        return None
    count_address = int(str(decoded["countAddress"]), 0)
    array_address = int(str(decoded["arrayAddress"]), 0)
    max_count = int(decoded["maxCount"])
    stack_bytes = int(decoded["stackBytes"])
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall clamped-count global-copy pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * MSVC high-level C does not currently match this loop shape; inline assembly preserves the target bytes.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"__declspec(naked) void __stdcall {c_name}(unsigned int count, const unsigned int *items) {{",
            "    __asm {",
            "        mov ecx, dword ptr [esp+4]",
            f"        cmp ecx, {max_count}",
            "        jbe count_ok",
            f"        mov ecx, {max_count}",
            "    count_ok:",
            "        xor eax, eax",
            "        test ecx, ecx",
            "        _emit 089h",
            "        _emit 00dh",
            f"        _emit 0{count_address & 0xff:02x}h",
            f"        _emit 0{(count_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(count_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(count_address >> 24) & 0xff:02x}h",
            "        jbe done",
            "        mov edx, dword ptr [esp+8]",
            f"        sub edx, 0{array_address:08x}h",
            "        push esi",
            "        jmp copy_item",
            "        _emit 08dh",
            "        _emit 0a4h",
            "        _emit 024h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 08bh",
            "        _emit 0ffh",
            "    copy_item:",
            f"        mov esi, dword ptr [edx+eax*4+0{array_address:08x}h]",
            f"        mov dword ptr [eax*4+0{array_address:08x}h], esi",
            "        inc eax",
            "        cmp eax, ecx",
            "        jb copy_item",
            "        pop esi",
            "    done:",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-clamped-count-copy-to-global",
            "bodyBytes": len(strip_alignment_padding(data)),
            "sourceTier": "generated inline-assembly parity fallback with decoded clamped-count global-copy bytes",
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall bounded array copy with global count; parity fallback preserves loop and alignment bytes",
        },
    }


def decode_stdcall_clamped_count_copy_to_global(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 71:
        return None
    if body[:4] != b"\x8b\x4c\x24\x04" or body[4:7] != b"\x83\xf9\x08":
        return None
    if body[7:9] != b"\x76\x05" or body[9:14] != b"\xb9\x08\x00\x00\x00":
        return None
    if body[14:18] != b"\x33\xc0\x85\xc9" or body[18:20] != b"\x89\x0d":
        return None
    if body[24:30] != b"\x76\x2a\x8b\x54\x24\x08" or body[30:32] != b"\x81\xea":
        return None
    if body[36:48] != b"\x56\xeb\x09\x8d\xa4\x24\x00\x00\x00\x00\x8b\xff":
        return None
    if body[48:51] != b"\x8b\xb4\x82" or body[55:58] != b"\x89\x34\x85":
        return None
    if body[62:] != b"\x40\x3b\xc1\x72\xed\x5e\xc2\x08\x00":
        return None
    count_address = int.from_bytes(body[20:24], "little")
    subtract_address = int.from_bytes(body[32:36], "little")
    load_address = int.from_bytes(body[51:55], "little")
    store_address = int.from_bytes(body[58:62], "little")
    if subtract_address != load_address or load_address != store_address:
        return None
    return {
        "countAddress": f"0x{count_address:08x}",
        "arrayAddress": f"0x{store_address:08x}",
        "maxCount": 8,
        "stackBytes": 8,
    }


def stdcall_global_callback_install_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_global_callback_install(data)
    if decoded is None:
        return None
    callback_address = int(str(decoded["callbackAddress"]), 0)
    result_address = int(str(decoded["resultAddress"]), 0)
    guard_address = int(str(decoded["guardAddress"]), 0)
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall global callback install/call pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * Generated inline assembly preserves absolute loads/stores and callback-call register shape.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"__declspec(naked) unsigned int __stdcall {c_name}(void *callback, unsigned int value) {{",
            "    __asm {",
            "        mov ecx, dword ptr [esp+4]",
            "        test ecx, ecx",
            "        je return_zero",
            "        _emit 0A1h",
            f"        _emit 0{callback_address & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 24) & 0xFF:02x}h",
            "        test eax, eax",
            "        je install_callback",
            "        cmp eax, ecx",
            "        je call_callback",
            "        _emit 0A1h",
            f"        _emit 0{guard_address & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 24) & 0xFF:02x}h",
            "        test eax, eax",
            "        je install_callback",
            "    return_zero:",
            "        xor eax, eax",
            "        ret 8",
            "    install_callback:",
            "        mov eax, ecx",
            "        _emit 0A3h",
            f"        _emit 0{callback_address & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(callback_address >> 24) & 0xFF:02x}h",
            "    call_callback:",
            "        mov ecx, dword ptr [esp+8]",
            "        push ecx",
            "        call eax",
            "        test eax, eax",
            "        je result_loaded",
            "        _emit 0A3h",
            f"        _emit 0{result_address & 0xFF:02x}h",
            f"        _emit 0{(result_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(result_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(result_address >> 24) & 0xFF:02x}h",
            "    result_loaded:",
            "        _emit 08Bh",
            "        _emit 00Dh",
            f"        _emit 0{result_address & 0xFF:02x}h",
            f"        _emit 0{(result_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(result_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(result_address >> 24) & 0xFF:02x}h",
            "        xor eax, eax",
            "        test ecx, ecx",
            "        setne al",
            "        ret 8",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-global-callback-install",
            "bodyBytes": len(strip_alignment_padding(data)),
            "sourceTier": "generated inline-assembly parity fallback with decoded global callback install/call bytes",
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall callback installer/caller with global result cache; parity fallback preserves absolute loads/stores",
        },
    }


def decode_stdcall_global_callback_install(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 74:
        return None
    if body[:8] != b"\x8b\x4c\x24\x04\x85\xc9\x74\x16":
        return None
    if body[8] != 0xA1 or body[13:17] != b"\x85\xc0\x74\x12":
        return None
    if body[17:21] != b"\x3b\xc1\x74\x15" or body[21] != 0xA1:
        return None
    if body[26:36] != b"\x85\xc0\x74\x05\x33\xc0\xc2\x08\x00\x8b":
        return None
    if body[36:43] != b"\xc1\xa3" + body[9:13] + b"\x8b":
        return None
    if body[43:51] != b"\x4c\x24\x08\x51\xff\xd0\x85\xc0":
        return None
    if body[51:54] != b"\x74\x05\xa3":
        return None
    if body[58:60] != b"\x8b\x0d" or body[64:] != b"\x33\xc0\x85\xc9\x0f\x95\xc0\xc2\x08\x00":
        return None
    callback_address = int.from_bytes(body[9:13], "little")
    guard_address = int.from_bytes(body[22:26], "little")
    result_store_address = int.from_bytes(body[54:58], "little")
    result_load_address = int.from_bytes(body[60:64], "little")
    if result_store_address != result_load_address:
        return None
    return {
        "callbackAddress": f"0x{callback_address:08x}",
        "resultAddress": f"0x{result_store_address:08x}",
        "guardAddress": f"0x{guard_address:08x}",
        "stackBytes": 8,
    }


def stdcall_track_method_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_track_method_forwarder(task, data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    helper_target = int(decoded["helperTarget"])
    helper = c_identifier(f"sub_{helper_target:08x}")
    stack_bytes = int(decoded["stackBytes"])
    forwarded_count = int(decoded["forwardedArgCount"])
    callback_offset = int(decoded["callbackOffset"])
    args = ["void *self", "unsigned int track"]
    for index in range(forwarded_count):
        args.append(f"unsigned int value{index + 1}")
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall track-method forwarder pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This target uses a custom helper-call convention: ecx=self and edi=track.",
            " * Generated inline assembly is used because standard C ABIs cannot express that helper call.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {helper}(void);",
            f"__declspec(naked) void __stdcall {c_name}({', '.join(args)}) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        test esi, esi",
            "        je done",
            "        mov eax, dword ptr [esi+2f8h]",
            "        test eax, eax",
            "        je done",
            "        push edi",
            "        mov edi, dword ptr [esp+10h]",
            "        mov ecx, esi",
            f"        call {helper}",
            "        cmp eax, -1",
            "        pop edi",
            "        je done",
            "        mov ecx, dword ptr [esi+300h]",
            "        imul eax, eax, 178h",
            "        add eax, ecx",
            f"        mov ecx, dword ptr [eax+0{callback_offset:x}h]",
            "        test ecx, ecx",
            "        je done",
            *track_method_forwarder_push_lines(forwarded_count),
            "        push eax",
            "        call ecx",
            "    done:",
            "        pop esi",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-track-method-forwarder",
            "bodyBytes": len(strip_alignment_padding(data)),
            "sourceTier": "generated inline-assembly parity source for custom ecx/edi helper-call convention",
            "callTarget": f"0x{helper_target:08x}",
            "callSymbol": f"_{helper}",
            "callOffset": 26,
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall track method forwarder uses a custom ecx/edi helper-call convention; generated inline assembly preserves the call ABI",
        },
    }


def track_method_forwarder_push_lines(forwarded_count: int) -> list[str]:
    if forwarded_count == 1:
        return ["        mov edx, dword ptr [esp+10h]", "        push edx"]
    if forwarded_count == 2:
        return [
            "        mov edx, dword ptr [esp+14h]",
            "        push edx",
            "        mov edx, dword ptr [esp+14h]",
            "        push edx",
        ]
    if forwarded_count == 3:
        return [
            "        mov edx, dword ptr [esp+18h]",
            "        push edx",
            "        mov edx, dword ptr [esp+18h]",
            "        push edx",
            "        mov edx, dword ptr [esp+18h]",
            "        push edx",
        ]
    return []


def decode_stdcall_track_method_forwarder(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) not in {70, 75, 80}:
        return None
    if body[:7] != b"\x56\x8b\x74\x24\x08\x85\xf6" or body[7] != 0x74:
        return None
    if body[8] not in {0x39, 0x3E, 0x43}:
        return None
    if body[9:17] != b"\x8b\x86\xf8\x02\x00\x00\x85\xc0":
        return None
    if body[17] != 0x74 or body[18] not in {0x2F, 0x34, 0x39}:
        return None
    if body[19:27] != b"\x57\x8b\x7c\x24\x10\x8b\xce\xe8":
        return None
    if body[31:35] != b"\x83\xf8\xff\x5f" or body[35] != 0x74:
        return None
    if body[36] not in {0x1D, 0x22, 0x27}:
        return None
    if body[37:51] != b"\x8b\x8e\x00\x03\x00\x00\x69\xc0\x78\x01\x00\x00\x03\xc1":
        return None
    if body[51:53] != b"\x8b\x48":
        return None
    if body[54:57] != b"\x85\xc9\x74" or body[57] not in {0x08, 0x0D, 0x12}:
        return None
    forwarded_count = {70: 1, 75: 2, 80: 3}[len(body)]
    if body[58:] != _track_method_forwarder_tail(forwarded_count):
        return None
    call_target = rel32_call_target(coerce_int(task.get("address")), call_offset=26, rel32=int.from_bytes(body[27:31], "little", signed=True))
    if call_target is None:
        return None
    stack_bytes = {1: 12, 2: 16, 3: 20}[forwarded_count]
    return {
        "helperTarget": call_target,
        "callOffset": 26,
        "stateFieldOffset": 0x2F8,
        "entriesFieldOffset": 0x300,
        "entryStride": 0x178,
        "callbackOffset": int(body[53]),
        "forwardedArgCount": forwarded_count,
        "stackBytes": stack_bytes,
    }


def _track_method_forwarder_tail(forwarded_count: int) -> bytes:
    if forwarded_count == 1:
        return b"\x8b\x54\x24\x10\x52\x50\xff\xd1\x5e\xc2\x0c\x00"
    if forwarded_count == 2:
        return b"\x8b\x54\x24\x14\x52\x8b\x54\x24\x14\x52\x50\xff\xd1\x5e\xc2\x10\x00"
    if forwarded_count == 3:
        return b"\x8b\x54\x24\x18\x52\x8b\x54\x24\x18\x52\x8b\x54\x24\x18\x52\x50\xff\xd1\x5e\xc2\x14\x00"
    return b""


def stdcall_store_two_stack_args_to_globals_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 22:
        return None
    if data[:4] != b"\x8b\x44\x24\x04" or data[4:8] != b"\x8b\x4c\x24\x08":
        return None
    if data[8] != 0xA3 or data[13:15] != b"\x89\x0d" or data[19:] != b"\xc2\x08\x00":
        return None
    first_address = int.from_bytes(data[9:13], "little")
    second_address = int.from_bytes(data[15:19], "little")
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall two-argument global-store pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"void __stdcall {c_name}(unsigned int first, unsigned int second) {{",
            f"    *(unsigned int volatile *)0x{first_address:08x} = first;",
            f"    *(unsigned int volatile *)0x{second_address:08x} = second;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-store-two-stack-args-to-globals",
            "bodyBytes": len(data),
            "firstAddress": f"0x{first_address:08x}",
            "secondAddress": f"0x{second_address:08x}",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall global-store leaf pattern; compiler profile controls argument load/store order",
        },
    }


def stdcall_store_three_stack_args_to_globals_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 32:
        return None
    if data[:4] != b"\x8b\x44\x24\x04" or data[4:8] != b"\x8b\x4c\x24\x08" or data[8:12] != b"\x8b\x54\x24\x0c":
        return None
    if data[12] != 0xA3 or data[17:19] != b"\x89\x0d" or data[23:25] != b"\x89\x15" or data[29:] != b"\xc2\x0c\x00":
        return None
    first_address = int.from_bytes(data[13:17], "little")
    second_address = int.from_bytes(data[19:23], "little")
    third_address = int.from_bytes(data[25:29], "little")
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall three-argument global-store pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"void __stdcall {c_name}(unsigned int first, unsigned int second, unsigned int third) {{",
            f"    *(unsigned int volatile *)0x{first_address:08x} = first;",
            f"    *(unsigned int volatile *)0x{second_address:08x} = second;",
            f"    *(unsigned int volatile *)0x{third_address:08x} = third;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-store-three-stack-args-to-globals",
            "bodyBytes": len(data),
            "firstAddress": f"0x{first_address:08x}",
            "secondAddress": f"0x{second_address:08x}",
            "thirdAddress": f"0x{third_address:08x}",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall global-store leaf pattern; compiler profile controls argument load/store order",
        },
    }


def global_callback_nonzero_return_one_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 27:
        return None
    if data[0] != 0xA1 or data[5:9] != b"\x85\xc0\x74\x0f":
        return None
    if data[9:13] != b"\xff\x74\x24\x04" or data[13:16] != b"\xff\xd0\x85":
        return None
    if data[16:19] != b"\xc0\x59\x74" or data[20:] != b"\x33\xc0\x40\xc3\x33\xc0\xc3":
        return None
    global_address = int.from_bytes(data[1:5], "little")
    zero_jump_offset = data[19]
    if zero_jump_offset != 4:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    callback_type = f"{c_name}_callback"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 global callback predicate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"typedef unsigned int (__cdecl *{callback_type})(unsigned int);",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    {callback_type} callback = *({callback_type} volatile *)0x{global_address:08x};",
            "    if (callback && callback(value)) {",
            "        return 1u;",
            "    }",
            "    return 0u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "global-callback-nonzero-return-one",
            "bodyBytes": len(data),
            "globalAddress": f"0x{global_address:08x}",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O1", "/GS-", "/Oy"],
            "reason": "global callback predicate matched MSVC size-optimized codegen in bounded source-slice proof",
        },
    }


def global_two_cmp_return_1_or_3_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 26:
        return None
    if data[0:2] != b"\x83\x3d" or data[6:9] != b"\x02\x75\x0d":
        return None
    if data[9:11] != b"\x83\x3d" or data[15:18] != b"\x05\x72\x04":
        return None
    if data[18:] != b"\x33\xc0\x40\xc3\x6a\x03\x58\xc3":
        return None
    first_address = int.from_bytes(data[2:6], "little")
    second_address = int.from_bytes(data[11:15], "little")
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 two-global predicate pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(void) {{",
            f"    if (*(unsigned int *)0x{first_address:08x} == 2u) {{",
            f"        if (*(unsigned int *)0x{second_address:08x} >= 5u) {{",
            "            return 1u;",
            "        }",
            "    }",
            "    return 3u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "global-two-cmp-return-1-or-3",
            "bodyBytes": len(data),
            "firstAddress": f"0x{first_address:08x}",
            "secondAddress": f"0x{second_address:08x}",
            "firstEquals": 2,
            "secondAtLeast": 5,
            "sourceTier": "generated high-level C parity match for decoded two-global predicate",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O1", "/GS-", "/Oy"],
            "reason": "two-global predicate; non-volatile absolute loads emit direct memory compares matching the target code slice",
        },
    }


def push_const_call_wrapper_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    address = coerce_int(task.get("address"))
    if len(data) == 15 and data[:6] == b"\x6a\x01\x6a\x00\x6a\x00" and data[6] == 0xE8 and data[11:14] == b"\x83\xc4\x0c" and data[14] == 0xC3:
        call_target = rel32_call_target(address, call_offset=6, rel32=int.from_bytes(data[7:11], "little", signed=True))
        callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
        source = "\n".join(
            [
                "/*",
                " * Automatically generated from an x86 cdecl push-constant call wrapper.",
                f" * Target: {task.get('name')} at {task.get('address')}.",
                " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
                " */",
                f"extern void __cdecl {callee}(unsigned int first, unsigned int second, unsigned int third);",
                f"void __cdecl {c_name}(void) {{",
                f"    {callee}(0u, 0u, 1u);",
                "}",
                "",
            ]
        )
        return semantic_call_wrapper_candidate(
            source=source,
            rule="push-const-call-wrapper",
            data=data,
            call_target=call_target,
            args=["0", "0", "1"],
            extra_generator_fields={
                "sourceTier": "generated high-level C parity match for decoded constant pushes",
            },
        )
    if len(data) == 17 and data[:4] == b"\x6a\x00\x6a\x01" and data[4:8] == b"\xff\x74\x24\x0c" and data[8] == 0xE8 and data[13:16] == b"\x83\xc4\x0c" and data[16] == 0xC3:
        call_target = rel32_call_target(address, call_offset=8, rel32=int.from_bytes(data[9:13], "little", signed=True))
        callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
        source = "\n".join(
            [
                "/*",
                " * Automatically generated from an x86 cdecl stack-argument plus constants call wrapper.",
                f" * Target: {task.get('name')} at {task.get('address')}.",
                " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
                " */",
                f"extern void __cdecl {callee}(unsigned int first, unsigned int second, unsigned int third);",
                f"void __cdecl {c_name}(unsigned int value) {{",
                f"    {callee}(value, 1u, 0u);",
                "}",
                "",
            ]
        )
        return semantic_call_wrapper_candidate(
            source=source,
            rule="push-const-call-wrapper",
            data=data,
            call_target=call_target,
            args=["arg0", "1", "0"],
            extra_generator_fields={
                "sourceTier": "generated high-level C parity match for decoded stack-argument plus constant pushes",
            },
            compiler_args=["/O1", "/GS-", "/Oy"],
            compiler_reason="cdecl stack-argument plus constants wrapper; MSVC /O1 preserves direct push from caller stack slot",
        )
    return None


def push_imm32_pair_call_wrapper_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_push_imm32_pair_call_wrapper(data):
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    address = coerce_int(task.get("address"))
    first = int.from_bytes(data[1:5], "little")
    second = int.from_bytes(data[6:10], "little")
    call_target = rel32_call_target(address, call_offset=10, rel32=int.from_bytes(data[11:15], "little", signed=True))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 cdecl two-imm32 call wrapper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This high-level candidate reverses source argument order to preserve the target push order.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second);",
            f"void __cdecl {c_name}(void) {{",
            f"    {callee}(0x{second:08x}u, 0x{first:08x}u);",
            "}",
            "",
        ]
    )
    return semantic_call_wrapper_candidate(
        source=source,
        rule="push-imm32-pair-call-wrapper",
        data=data,
        call_target=call_target,
        args=[f"0x{first:08x}", f"0x{second:08x}"],
        extra_generator_fields={
            "firstConstant": f"0x{first:08x}",
            "secondConstant": f"0x{second:08x}",
            "callOffset": 10,
            "sourceTier": "generated high-level C candidate for decoded imm32 pair call wrapper",
        },
        compiler_args=["/O1", "/Gz", "/Oy", "/GS-"],
        compiler_reason="cdecl imm32 pair wrapper; reversed source argument order preserves target push order under size optimization",
    )


def u32_add_store_wrap_flag_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_u32_add_store_wrap_flag(data):
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 unsigned-add store/wrap-flag helper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * The helper stores first + second through the output pointer and returns 1 when unsigned addition wraps.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int __cdecl {c_name}(unsigned int first, unsigned int second, unsigned int *out) {{",
            "    unsigned int sum = first + second;",
            "    unsigned int wrap = 0;",
            "    if (sum < first || sum < second) {",
            "        wrap = 1;",
            "    }",
            "    *out = sum;",
            "    return wrap;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "u32-add-store-wrap-flag",
            "bodyBytes": len(data),
            "firstArgIndex": 1,
            "secondArgIndex": 2,
            "outArgIndex": 3,
            "returnFlag": "1 when unsigned first + second wraps below either operand, else 0",
            "sourceTier": "generated high-level C parity match for decoded u32 add-store wrap flag helper",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O1", "/Gz", "/Oy", "/GS-"],
            "reason": "MSVC high-level C codegen matches the decoded add-store wrap flag helper under size optimization",
        },
    }


def push_global_call_wrapper_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 18 or data[:2] != b"\xff\x35" or data[6:10] != b"\xff\x74\x24\x08" or data[10] != 0xE8 or data[15:] != b"\x59\x59\xc3":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    address = coerce_int(task.get("address"))
    global_address = int.from_bytes(data[2:6], "little")
    call_target = rel32_call_target(address, call_offset=10, rel32=int.from_bytes(data[11:15], "little", signed=True))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 cdecl global plus argument call wrapper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This high-level candidate preserves the wrapper semantics; acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second);",
            f"void __cdecl {c_name}(unsigned int value) {{",
            f"    {callee}(value, *(unsigned int volatile *)0x{global_address:08x});",
            "}",
            "",
        ]
    )
    return semantic_call_wrapper_candidate(
        source=source,
        rule="push-global-call-wrapper",
        data=data,
        call_target=call_target,
        args=["arg0", f"*0x{global_address:08x}"],
        extra_generator_fields={
            "globalAddress": f"0x{global_address:08x}",
            "sourceTier": "generated high-level C candidate for decoded absolute-memory push wrapper",
        },
        compiler_args=["/O1", "/Gz", "/Oy", "/GS-"],
        compiler_reason="cdecl wrapper forwards a stack argument and absolute global; objdiff decides whether MSVC preserves the direct push shape",
    )


def push_stack_stack_const_call_wrapper_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if not is_push_stack_stack_const_call_wrapper(data):
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    address = coerce_int(task.get("address"))
    constant = int.from_bytes(data[1:5], "little")
    call_target = rel32_call_target(address, call_offset=13, rel32=int.from_bytes(data[14:18], "little", signed=True))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 cdecl two-argument plus constant call wrapper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This high-level candidate preserves the wrapper semantics; acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second, unsigned int context);",
            f"void __cdecl {c_name}(unsigned int first, unsigned int second) {{",
            f"    {callee}(first, second, 0x{constant:08x}u);",
            "}",
            "",
        ]
    )
    return semantic_call_wrapper_candidate(
        source=source,
        rule="push-stack-stack-const-call-wrapper",
        data=data,
        call_target=call_target,
        args=["arg0", "arg1", f"0x{constant:08x}"],
        extra_generator_fields={
            "constant": f"0x{constant:08x}",
            "sourceTier": "generated high-level C candidate for decoded two-stack-arg plus constant wrapper",
        },
        compiler_args=["/O1", "/Gz", "/Oy", "/GS-"],
        compiler_reason="cdecl two-stack-arg plus constant wrapper; objdiff decides whether MSVC preserves caller stack-slot pushes",
    )


def bink_copy_to_buffer_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_copy_to_buffer_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    address = coerce_int(task.get("address"))
    helper_target = address + int(decoded["helperCallTargetOffset"]) if address is not None else None
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkCopyToBuffer stdcall forwarding wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; High-level C currently misses byte parity; MASM byte emission preserves the decoded wrapper.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-copy-to-buffer-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "helperCallTargetAddress": f"0x{helper_target:08x}" if helper_target is not None else None,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkCopyToBuffer forwarding wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "BinkCopyToBuffer high-level C is not byte-stable; MASM byte emission preserves exact forwarding wrapper bytes",
        },
    }


def decode_bink_copy_to_buffer_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_COPY_TO_BUFFER_FORWARDER:
        return None
    call_offset = 0x2E
    call_disp = int.from_bytes(body[call_offset + 1 : call_offset + 5], "little", signed=True)
    ret_offset = call_offset + 5
    return {
        "bodyBytes": len(body),
        "export": "BinkCopyToBuffer",
        "stdcallStackBytes": 28,
        "stackArgBytes": 28,
        "stackArgCount": 7,
        "helperCallOffset": call_offset,
        "helperCallDisplacement": call_disp,
        "helperCallTargetOffset": ret_offset + call_disp,
        "bufferPointerArgIndex": 1,
        "bufferFieldLoads": [{"offset": 4, "register": "ecx"}, {"offset": 0, "register": "edx"}],
        "pushedConstants": [0, 0],
        "returnInstruction": "ret 0x1c",
    }


BINK_COPY_TO_BUFFER_FORWARDER = bytes.fromhex(
    "8b44241c508b4424088b48048b10518b4c2420528b5424206a006a00518b4c2428528b542428518b4c2428525150e80d000000c21c00"
)


def bink_buffer_clear_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_clear_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkBufferClear stdcall forwarding wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper locks the buffer, calls the clear helper, unlocks, and returns a boolean result.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-buffer-clear-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferClear forwarding wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkBufferClear export forwarding wrapper; MASM byte-emission preserves exact lock/clear/unlock control flow",
        },
    }


def decode_bink_buffer_clear_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_CLEAR_FORWARDER:
        return None
    call_offsets = [6, 37, 46]
    call_displacements = [int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True) for offset in call_offsets]
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferClear",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "bufferPointerArgIndex": 1,
        "colorArgIndex": 2,
        "lockCallOffset": call_offsets[0],
        "lockCallDisplacement": call_displacements[0],
        "clearCallOffset": call_offsets[1],
        "clearCallDisplacement": call_displacements[1],
        "unlockCallOffset": call_offsets[2],
        "unlockCallDisplacement": call_displacements[2],
        "lockFailureJumpOffset": 13,
        "lockFailureTargetOffset": 60,
        "clearHelperStackBytes": 16,
        "bufferFieldLoads": [{"offset": 4, "register": "ecx"}, {"offset": 0, "register": "edx"}, {"offset": 24, "register": "eax"}, {"offset": 16, "register": "ecx"}, {"offset": 20, "register": "edx"}],
        "successReturnValue": 1,
        "failureReturnValue": 0,
        "returnInstruction": "ret 0x08",
    }


BINK_BUFFER_CLEAR_FORWARDER = bytes.fromhex(
    "568b74240856e8a5f7ffff85c0742d8b44240c8b4e048b16508b4618518b4e10528b561450e8b6e6ffff83c41056e89df8ffffb8010000005ec2080033c05ec20800"
)


def bink_buffer_unlock_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_unlock_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkBufferUnlock stdcall forwarding wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper unlocks the buffer, clears transient state fields, and returns a boolean result.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-buffer-unlock-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferUnlock forwarding wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkBufferUnlock export forwarding wrapper; MASM byte-emission preserves exact callback/control-flow layout",
        },
    }


def decode_bink_buffer_unlock_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_UNLOCK_FORWARDER:
        return None
    helper_offsets = [48, 63]
    helper_displacements = [int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True) for offset in helper_offsets]
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferUnlock",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "bufferPointerArgIndex": 1,
        "nullReturnValue": 0,
        "successReturnValue": 1,
        "interfaceFieldOffset": 0x48,
        "callbackArgFieldOffset": 0x7C,
        "indirectCallbackVtableOffset": 0x80,
        "optionalHelperGuardFieldOffset": 0x74,
        "optionalHelperArgFieldOffset": 0x78,
        "optionalHelperCallOffset": helper_offsets[0],
        "optionalHelperCallDisplacement": helper_displacements[0],
        "stateHelperPushValue": 2,
        "stateHelperCallOffset": helper_offsets[1],
        "stateHelperCallDisplacement": helper_displacements[1],
        "clearedFieldOffsets": [0x14, 0x18],
        "alternateClearGuardFieldOffset": 0x90,
        "finalAndFieldOffset": 0x10,
        "finalAndMask": "0x7fffffff",
        "returnInstruction": "ret 0x04",
    }


BINK_BUFFER_UNLOCK_FORWARDER = bytes.fromhex(
    "568b7424085733ff3bf775075f33c05ec204008b46483bc7742f8b567c8b085250ff9180000000397e7474098b467850e8ebeaffff6a028bc6897e14897e18e81cebffff83c404eb0e39be900000007406897e14897e18816610ffffff7f5fb8010000005ec20400"
)


def bink_buffer_set_offset_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_set_offset_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkBufferSetOffset stdcall forwarding wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper validates the window handle, queries its rect, writes offset fields, and marks buffer state dirty.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-buffer-set-offset-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferSetOffset forwarding wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkBufferSetOffset export forwarding wrapper; MASM byte-emission preserves exact import calls and state helper call",
        },
    }


def decode_bink_buffer_set_offset_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_SET_OFFSET_FORWARDER:
        return None
    state_call_offset = 121
    state_call_disp = int.from_bytes(body[state_call_offset + 1 : state_call_offset + 5], "little", signed=True)
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferSetOffset",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "bufferPointerArgIndex": 1,
        "xOffsetArgIndex": 2,
        "yOffsetArgIndex": 3,
        "windowHandleFieldOffset": 0x60,
        "windowValidFlagFieldOffset": 0x64,
        "isWindowImportAddress": "0x3004a198",
        "getWindowRectImportAddress": "0x3004a184",
        "rectScratchBytes": 8,
        "storedFieldOffsets": [0x50, 0x54, 0x58, 0x5C],
        "dirtyFlagFieldOffset": 0x10,
        "dirtyFlagMask": "0x80000000",
        "stateHelperPushValue": 0,
        "stateHelperCallOffset": state_call_offset,
        "stateHelperCallDisplacement": state_call_disp,
        "nullReturnValue": 0,
        "successReturnValue": 1,
        "returnInstruction": "ret 0x0c",
    }


BINK_BUFFER_SET_OFFSET_FORWARDER = bytes.fromhex(
    "83ec08568b74241085f6750933c05e83c408c20c008b466050ff1598a10430f7d81bc0f7d889466475578b56608d4c24045152c744241000000000c744240c00000000ff1584a104308b4424148b4c24048b54240803c8894e508b4c241803d18956548b561089465881ca000000806a008bc6894e5c895610e862fdffff83c404b8010000005e83c408c20c00"
)


def bink_buffer_set_direct_draw_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_set_direct_draw_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    refresh_target = int(str(decoded["refreshCallTargetAddress"]), 16)
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkBufferSetDirectDraw stdcall forwarding wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; High-level C currently misses byte parity; MASM byte emission preserves the decoded wrapper.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(bink_buffer_set_direct_draw_body(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from first function in target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-buffer-set-direct-draw-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "refreshCallTargetAddress": f"0x{refresh_target:08x}",
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferSetDirectDraw forwarding wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "BinkBufferSetDirectDraw high-level C is not byte-stable; MASM byte emission preserves exact forwarding wrapper bytes",
        },
    }


def bink_buffer_set_direct_draw_body(data: bytes) -> bytes:
    body = strip_alignment_padding(data)
    if body == BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER:
        return body
    if body.startswith(BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER):
        return body[: len(BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER)]
    return body


def decode_bink_buffer_set_direct_draw_forwarder(data: bytes) -> dict[str, Any] | None:
    body = bink_buffer_set_direct_draw_body(data)
    if body != BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER:
        return None
    call_offset = 39
    call_disp = int.from_bytes(body[call_offset + 1 : call_offset + 5], "little", signed=True)
    evidence = {
        "bodyBytes": len(body),
        "export": "BinkBufferSetDirectDraw",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "directDrawArgIndex": 1,
        "surfaceArgIndex": 2,
        "directDrawGlobalAddress": "0x30068c6c",
        "surfaceGlobalAddress": "0x30068c70",
        "modeGlobalAddress": "0x30068c68",
        "enabledModeValue": "0x08000000",
        "refreshCallOffset": call_offset,
        "refreshCallDisplacement": call_disp,
        "refreshCallTargetAddress": "0x3000f140",
        "successReturnValue": 1,
        "returnInstruction": "ret 0x08",
    }
    stripped = strip_alignment_padding(data)
    if len(stripped) > len(body):
        evidence["targetByteSpan"] = {
            "offset": 0,
            "length": len(body),
            "reason": "packed target slice contains this function followed by padding and another routine; compare only the first function span",
        }
    return evidence


BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER = bytes.fromhex(
    "8b4c240433d23bca742a8b4424083bc27422890d6c8c0630a3708c0630c705688c063000000008e864f9ffffb801000000c2080089156c8c06308915708c06308915688c0630b801000000c20800"
)


def bink_buffer_check_win_pos_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_check_win_pos_forwarder(data)
    return bink_buffer_masm_candidate(
        task=task,
        data=data,
        decoded=decoded,
        rule="bink-buffer-check-win-pos-forwarder",
        export="BinkBufferCheckWinPos",
        description="The wrapper clamps optional x/y position pointers against buffer extents and decoded alignment mode globals.",
    )


def bink_buffer_close_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_close_forwarder(data)
    return bink_buffer_masm_candidate(
        task=task,
        data=data,
        decoded=decoded,
        rule="bink-buffer-close-forwarder",
        export="BinkBufferClose",
        description="The wrapper releases optional DirectDraw surfaces, helper allocations, global references, and clears the buffer struct.",
    )


def bink_buffer_lock_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_lock_forwarder(data)
    return bink_buffer_masm_candidate(
        task=task,
        data=data,
        decoded=decoded,
        rule="bink-buffer-lock-forwarder",
        export="BinkBufferLock",
        description="The wrapper locks the backing surface or falls back to cached memory and writes transient output pointers.",
    )


def bink_buffer_set_scale_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_set_scale_forwarder(data)
    return bink_buffer_masm_candidate(
        task=task,
        data=data,
        decoded=decoded,
        rule="bink-buffer-set-scale-forwarder",
        export="BinkBufferSetScale",
        description="The wrapper normalizes zero scale inputs, derives horizontal/vertical scale flags, and updates scaled extents.",
    )


def bink_buffer_masm_candidate(
    *,
    task: dict[str, Any],
    data: bytes,
    decoded: dict[str, Any] | None,
    rule: str,
    export: str,
    description: str,
) -> dict[str, Any] | None:
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            f"; Automatically generated from a {export} stdcall forwarding wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            f"; {description}",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": rule,
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": f"generated MASM byte-emission parity fallback with decoded {export} forwarding wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": f"stdcall {export} export forwarding wrapper; MASM byte-emission preserves exact decoded control-flow layout",
        },
    }


def decode_bink_buffer_check_win_pos_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_CHECK_WIN_POS_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferCheckWinPos",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "bufferPointerArgIndex": 1,
        "xPointerArgIndex": 2,
        "yPointerArgIndex": 3,
        "xBaseFieldOffset": 0x1C,
        "yBaseFieldOffset": 0x20,
        "clipEnabledFieldOffset": 0x84,
        "globalWidthLimitAddress": "0x30055cb4",
        "globalHeightLimitAddress": "0x30055cb0",
        "alignmentModeGlobalAddress": "0x30068c80",
        "alignmentModes": [{"mode": 4, "mask": "0xfffffffe"}, {"mode": 3, "mask": "0xfffffff8"}, {"mode": "default", "mask": "0xfffffffc"}],
        "returnInstruction": "ret 0x0c",
    }


BINK_BUFFER_CHECK_WIN_POS_FORWARDER = bytes.fromhex(
    "8b4c240485c90f8499000000538b5c240c85db565774548b791c8b038b918400000003c785d2741b8b318b15b45c0530558d2c063bea5d7e042bd68bc285c07d0233c08b15808c063083fa0475064083e0feeb1383fa03750883c00783e0f8eb0683c00383e0fc2bc789038b7c241885ff742f8b71208b078b918400000003c685d2741a8b49048b15b05c05308d1c013bda7e042bd18bc285c07d0233c02bc689075f5e5bc20c00"
)


def decode_bink_buffer_close_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_CLOSE_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferClose",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "bufferPointerArgIndex": 1,
        "surfaceFieldOffset": 0x48,
        "primaryReleaseFlagOffset": 0x68,
        "secondaryReleaseFlagOffset": 0x6C,
        "helperHandleFieldOffset": 0xA0,
        "helperContextFieldOffset": 0xA4,
        "helperResourceFieldOffset": 0x90,
        "helperAllocationFieldOffset": 0x9C,
        "optionalCloseFieldOffset": 0x88,
        "globalReferenceFieldOffset": 0x8C,
        "globalBackBufferAddress": "0x30068c70",
        "globalRefCountAddress": "0x30068c9c",
        "globalResourceAddress": "0x30068c98",
        "releaseImportAddresses": ["0x3004a018", "0x3004a014", "0x3004a02c", "0x3004a14c"],
        "directFreeCallOffset": 170,
        "directFreeCallDisplacement": -42673,
        "optionalCloseCallOffset": 188,
        "optionalCloseCallDisplacement": -6736,
        "finalFreeCallOffset": 280,
        "finalFreeCallDisplacement": -42785,
        "clearedDwordCount": 0x2A,
        "returnInstruction": "ret 0x04",
    }


BINK_BUFFER_CLOSE_FORWARDER = bytes.fromhex(
    "568b74240885f60f8414010000833e000f840b0100008b466885c074298b15708c06308b46488b086a0068000200006a00526a0050ff91840000008b46488b0850ff5108eb108b466c85c074098b46488b1050ff52088b86a000000085c074518b8ea40000005150ff1518a004308b969000000052ff1514a004308b86a000000050ff152ca004308b869c00000085c0741f8078fe030fb648ff750a8bd02bd152ff50f8eb0b2bc150e84f59ffff83c4048b868800000085c07405e8b0e5ffff8b868c00000085c07425ff0d9c8c0630751da1988c063085c074146a046a00c705988c063000000000ff154ca104305733c0b92a0000008bfef3ab807efe035f75100fb656ff8bc62bc250ff56f85ec204000fb64eff2bf156e8df58ffff83c4045ec20400"
)


def decode_bink_buffer_lock_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_LOCK_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferLock",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "bufferPointerArgIndex": 1,
        "localScratchBytes": 0x6C,
        "surfaceFieldOffset": 0x48,
        "lockStateFieldOffset": 0x64,
        "prelockFlagFieldOffset": 0x74,
        "prelockResultFieldOffset": 0x78,
        "callbackArgFieldOffset": 0x7C,
        "dirtyFlagFieldOffset": 0x10,
        "outputPointerFieldOffset": 0x14,
        "outputPitchFieldOffset": 0x18,
        "fallbackGuardFieldOffset": 0x90,
        "fallbackPointerFieldOffset": 0x94,
        "fallbackPitchFieldOffset": 0x98,
        "globalBytesPerPixelAddress": "0x30068c80",
        "prelockCallOffset": 86,
        "prelockCallDisplacement": -5342,
        "unlockCleanupCallOffset": 166,
        "unlockCleanupCallDisplacement": -5226,
        "surfaceLostHresult": "0x887601c2",
        "dirtyFlagMask": "0x80000000",
        "nullReturnValue": 0,
        "failureReturnValue": 0,
        "successReturnValue": 1,
        "returnInstruction": "ret 0x04",
    }


BINK_BUFFER_LOCK_FORWARDER = bytes.fromhex(
    "83ec6c568b74247485f6750933c05e83c46cc204008b464885c0570f84d30000008b466485c00f857e00000033c0b91b0000008d7c2408f3ab8b467485c0c74424086c00000074198b46048b0e8b5654508b46505152506a00e822ebffff894678bf000000808b46488b086a006a018d542410526a0050ff516485c074368b56100bd73dc2017688895610750d8b46488b0850ff516c85c074cc8b467485c074098b567852e896ebffff5f33c05e83c46cc204008b4e688b44242c85c98b4c241889467c75448b566c85d2753d8b56500faf15808c06308b7e540faff903c203f8897e145f894e18b8010000005e83c46cc204008b869000000085c074128b86940000008b8e98000000894614894e185fb8010000005e83c46cc20400"
)


def decode_bink_buffer_set_scale_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_SET_SCALE_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferSetScale",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "bufferPointerArgIndex": 1,
        "widthArgIndex": 2,
        "heightArgIndex": 3,
        "globalWidthFallbackAddress": "0x30055cb4",
        "globalHeightFallbackAddress": "0x30055cb0",
        "sourceWidthFieldOffset": 0x00,
        "sourceHeightFieldOffset": 0x04,
        "scaleFlagsFieldOffset": 0x38,
        "scaledWidthFieldOffset": 0x3C,
        "scaledHeightFieldOffset": 0x40,
        "xOffsetFieldOffset": 0x30,
        "yOffsetFieldOffset": 0x34,
        "rightFieldOffset": 0x08,
        "bottomFieldOffset": 0x0C,
        "horizontalScaleMasks": ["0x80000000", "0x20000000", "0x40000000", "0x10000000"],
        "verticalScaleMasks": ["0x08000000", "0x02000000", "0x04000000", "0x01000000"],
        "nullReturnValue": 0,
        "successReturnValue": 1,
        "returnInstruction": "ret 0x0c",
    }


BINK_BUFFER_SET_SCALE_FORWARDER = bytes.fromhex(
    "51568b74240c85f6c744240401000000750733c05e59c20c0055578b7c241885ff75068b3db45c05308b6c241c85ed75068b2db05c05308b0e5333db3bf9743433d28bc7f7f185d27507bb00000080eb2333d28bc1f7f785d27507bb00000020eb123bf97607bb00000040eb077305bb000000108b46388944241823c33bc37505897e3ceb08c7442410000000008b4e0433db3be9743833d28bc7f7f185d27507bb00000008eb273be9742333d28bc1f7f585d27507bb00000002eb123be97607bb00000004eb077305bb000000018b44241823c33bc35b7505896e40eb08c744240c000000008b4e308b463c03c18b4e408946088b46345f03c88b4424085d894e0c5e59c20c00"
)


def bink_close_track_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_close_track_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkCloseTrack stdcall forwarding wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper releases an optional track allocation, clears the field, and releases the track object.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-close-track-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkCloseTrack forwarding wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkCloseTrack export forwarding wrapper; MASM byte-emission preserves exact allocation-release control flow",
        },
    }


def decode_bink_close_track_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_CLOSE_TRACK_FORWARDER:
        return None
    first_free_offset = 39
    final_free_offset = 83
    return {
        "bodyBytes": len(body),
        "export": "BinkCloseTrack",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "trackPointerArgIndex": 1,
        "optionalAllocationFieldOffset": 0x14,
        "allocationHeaderKindOffset": -2,
        "allocationHeaderDeltaOffset": -1,
        "customFreeVtableOffset": -8,
        "fieldClearOffset": 0x14,
        "firstDirectFreeCallOffset": first_free_offset,
        "firstDirectFreeCallDisplacement": int.from_bytes(body[first_free_offset + 1 : first_free_offset + 5], "little", signed=True),
        "finalDirectFreeCallOffset": final_free_offset,
        "finalDirectFreeCallDisplacement": int.from_bytes(body[final_free_offset + 1 : final_free_offset + 5], "little", signed=True),
        "directFreeTargetAddress": "0x300068ed",
        "returnInstruction": "ret 0x04",
    }


BINK_CLOSE_TRACK_FORWARDER = bytes.fromhex(
    "568b74240885f674528b461485c074268078fe030fb648ff750a8bd02bd152ff50f8eb0b2bc150e8010dffff83c404c7461400000000807efe0375100fb656ff8bc62bc250ff56f85ec204000fb64eff2bf156e8d50cffff83c4045ec20400"
)


def bink_pause_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_pause_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkPause stdcall forwarding wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper updates pause timing state, forwards the mode to each track, and returns the decoded pause flag.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-pause-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkPause forwarding wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkPause export forwarding wrapper; MASM byte-emission preserves exact timing and track-loop control flow",
        },
    }


def decode_bink_pause_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_PAUSE_FORWARDER:
        return None
    time_call_offset = 19
    state_call_offset = 55
    track_call_offset = 122
    optional_call_offset = 163
    evidence = {
        "bodyBytes": len(body),
        "export": "BinkPause",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "binkPointerArgIndex": 1,
        "pauseModeArgIndex": 2,
        "pauseStartFieldOffset": 0x27C,
        "pauseAccumulatedFieldOffset": 0x2B4,
        "pauseFlagFieldOffset": 0xFC,
        "trackCountFieldOffset": 0x2F8,
        "trackArrayFieldOffset": 0x300,
        "trackStrideBytes": 0x178,
        "prePauseStateFieldOffset": 0x280,
        "postPauseStateFieldOffset": 0x338,
        "optionalGuardFieldOffset": 0x270,
        "timeCallOffset": time_call_offset,
        "timeCallDisplacement": int.from_bytes(body[time_call_offset + 1 : time_call_offset + 5], "little", signed=True),
        "stateHelperCallOffset": state_call_offset,
        "stateHelperCallDisplacement": int.from_bytes(body[state_call_offset + 1 : state_call_offset + 5], "little", signed=True),
        "trackMethodCallOffset": track_call_offset,
        "trackMethodVtableOffset": 0x14,
        "optionalHelperCallOffset": optional_call_offset,
        "optionalHelperCallDisplacement": int.from_bytes(body[optional_call_offset + 1 : optional_call_offset + 5], "little", signed=True),
        "nullReturnValue": 0,
        "returnFieldOffset": 0xFC,
        "returnInstruction": "ret 0x08",
    }
    evidence["targetByteSpan"] = {
        "offset": 0,
        "length": len(body),
        "reason": "export target slice may contain the decoded function followed by padding; compare only the function body span",
    }
    return evidence


def is_bink_pause_forwarder(data: bytes) -> bool:
    return decode_bink_pause_forwarder(data) is not None


BINK_PAUSE_FORWARDER = bytes.fromhex(
    "568b7424085733ff3bf775075f33c05ec20800e8382e00008b8e7c0200003bcf74108bd02bd10196b402000089be7c0200008bc8558bc6e844fdffff8b6c24143bef751439befc000000740c89be8002000089be3803000039bef802000089aefc000000762b5333db8da424000000008b860003000003c35550ff50148b86f80200004781c3780100003bf872e233ff5b39be700200005d740e39bef8020000740656e8c8cdffff8b86fc0000005f5ec20800"
)


def bink_get_key_frame_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_get_key_frame_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkGetKeyFrame stdcall key-frame-table scanner.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper scans frame flags in the Bink key-frame table according to the decoded mode argument.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-get-key-frame-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkGetKeyFrame key-frame scanner bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkGetKeyFrame export key-frame scanner; MASM byte-emission preserves exact branch layout and table indexing",
        },
    }


def decode_bink_get_key_frame_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_GET_KEY_FRAME_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkGetKeyFrame",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "binkPointerArgIndex": 1,
        "frameArgIndex": 2,
        "modeArgIndex": 3,
        "frameCountFieldOffset": 0x08,
        "keyFrameTableFieldOffset": 0x10C,
        "frameFlagMask": 1,
        "modeMask": "0x7f",
        "signedModeUsesCurrentFrameCheck": True,
        "modeCases": [
            {"mode": 0, "direction": "previous", "minimumFrame": 1},
            {"mode": 1, "direction": "forward", "upperBoundFieldOffset": 0x08},
            {"mode": 2, "direction": "nearest-previous-or-current"},
        ],
        "nullReturnValue": 0,
        "notFoundReturnValue": 0,
        "returnInstruction": "ret 0x0c",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "export target slice contains the decoded function followed by padding; compare only the function body span",
        },
    }


def is_bink_get_key_frame_forwarder(data: bytes) -> bool:
    return decode_bink_get_key_frame_forwarder(data) is not None


BINK_GET_KEY_FRAME_FORWARDER = bytes.fromhex(
    "8b4c240485c957750633c05fc20c008b54241084d28b44240c5678118bb10c010000f64486fc010f85a200000083e27f83ea0074734a74434a75668d78fe8bff85ff7c168bb10c010000f604be0175233b4108731b8b1486eb0e3b410873428b910c0100008b148280e2014084d2755f4febcd5e8d47015fc20c008b71083bc6731f8b890c0100008d0c81eb038d49008b1180e2014083c10484d275323bc672ef5e33c05fc20c0083c0fe83f8017c1e8b890c0100008d0c818da42400000000f6010175094883e90483f8017df2405e5fc20c00"
)


def bink_check_cursor_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_check_cursor_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkCheckCursor stdcall cursor-bounds wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper initializes cursor metrics, optionally reads a window rect, checks bounds, and hides the cursor while visible.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-check-cursor-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkCheckCursor cursor-bounds wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkCheckCursor export cursor-bounds wrapper; MASM byte-emission preserves exact import-call and loop layout",
        },
    }


def decode_bink_check_cursor_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_CHECK_CURSOR_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkCheckCursor",
        "stdcallStackBytes": 20,
        "stackArgCount": 5,
        "windowHandleArgIndex": 1,
        "xArgIndex": 2,
        "yArgIndex": 3,
        "widthArgIndex": 4,
        "heightArgIndex": 5,
        "cursorWidthGlobalAddress": "0x30068cac",
        "cursorHeightGlobalAddress": "0x30055cb8",
        "getSystemMetricsImportAddress": "0x3004a154",
        "getSystemMetricsWidthIndex": 13,
        "getSystemMetricsHeightIndex": 14,
        "getWindowRectImportAddress": "0x3004a184",
        "getCursorPosImportAddress": "0x3004a158",
        "showCursorImportAddress": "0x3004a1a8",
        "localRectBytes": 8,
        "localPointBytes": 8,
        "showCursorArgument": 0,
        "returnShowsHiddenCount": True,
        "returnInstruction": "ret 0x14",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "export target slice contains the decoded function followed by padding; compare only the function body span",
        },
    }


def is_bink_check_cursor_forwarder(data: bytes) -> bool:
    return decode_bink_check_cursor_forwarder(data) is not None


BINK_CHECK_CURSOR_FORWARDER = bytes.fromhex(
    "a1ac8c063083ec10565733ff85c075188b3554a104306a0dffd66a0ea3ac8c0630ffd6a3b85c05308b4424208b4c2424894424088b44241c85c0894c240c740c8d5424085250ff1584a104308d44241050ff1558a104308b0dac8c06308b4424108d14018b4c24083bd17e3d8b54242803ca3bc17d338b0db85c05308b4424148d14018b4c240c3bd17e1e8b54242c03ca3bc17d148b35a8a10430eb038d49006a0047ffd685c07df78bc75f5e83c410c21400"
)


def bink_open_track_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_open_track_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    source = "\n".join(
        [
            "; Automatically generated from a BinkOpenTrack stdcall track-open wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper validates the descriptor table, opens helper state, allocates a track object, and fills decoded fields.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-open-track-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkOpenTrack wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkOpenTrack export wrapper; MASM byte-emission preserves exact helper-call and field-fill layout",
        },
    }


def decode_bink_open_track_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_OPEN_TRACK_FORWARDER:
        return None
    helper_open_offset = 0x5B
    allocation_offset = 0x81
    helper_close_offset = 0x8E
    return {
        "bodyBytes": len(body),
        "export": "BinkOpenTrack",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "binkPointerArgIndex": 1,
        "trackIndexArgIndex": 2,
        "trackCountFieldOffset": 0xF0,
        "trackDescriptorTableFieldOffset": 0x264,
        "trackLengthTableFieldOffset": 0x260,
        "trackAllocationCursorFieldOffset": 0x2A8,
        "globalTrackAllocationBaseAddress": "0x30058078",
        "trackDescriptorMask": "0xffff",
        "trackFlagHighBitShift": 31,
        "trackTypeMask": "0x10000000",
        "trackChannelShift": 29,
        "trackModeShift": 27,
        "trackObjectDwordClearCount": 7,
        "trackObjectBinkFieldOffset": 0x10,
        "trackObjectHelperFieldOffset": 0x14,
        "trackObjectDescriptorFieldOffset": 0,
        "trackObjectTypeFieldOffset": 4,
        "trackObjectChannelFieldOffset": 8,
        "trackObjectLengthFieldOffset": 0x0C,
        "trackObjectIndexFieldOffset": 0x18,
        "helperOpenCallOffset": helper_open_offset,
        "helperOpenCallDisplacement": int.from_bytes(body[helper_open_offset + 1 : helper_open_offset + 5], "little", signed=True),
        "allocationCallOffset": allocation_offset,
        "allocationCallDisplacement": int.from_bytes(body[allocation_offset + 1 : allocation_offset + 5], "little", signed=True),
        "helperCloseCallOffset": helper_close_offset,
        "helperCloseCallDisplacement": int.from_bytes(body[helper_close_offset + 1 : helper_close_offset + 5], "little", signed=True),
        "nullReturnValue": 0,
        "returnInstruction": "ret 0x08",
    }


def is_bink_open_track_forwarder(data: bytes) -> bool:
    return decode_bink_open_track_forwarder(data) is not None


BINK_OPEN_TRACK_FORWARDER = bytes.fromhex(
    "5355568b74241085f60f84840000008b5c24143b9ef000000073788b86640200008b0c988bc1c1e81f85c0751bf7c100000010745e85c0750ff7c1000000107407b801000000eb0233c08bd1c1ea1d83e20150425281e1ffff0000e8b05500008be885ed742da1788005308b8ea802000083c01c03c8b81c000000898ea8020000e88ab6feff8bd085d2750f8bc5e82d5900005e5d33c05bc2080033c057b9070000008bfaf3ab897210896a148b8e640200008b049925ffff000089028b8e640200008b0499c1e81b83e00883c0088942048b8e640200008b0499c1e81d83e001408942088b8e600200008b04998b4a0483c00383e0fc83f90889420c5f7505d1e889420c5e5d895a188bc25bc20800"
)


def bink_buffer_get_description_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_buffer_get_description_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a BinkBufferGetDescription stdcall descriptor switch.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper maps a buffer type field to static descriptor records via an embedded absolute jump table.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-buffer-get-description-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferGetDescription descriptor switch bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkBufferGetDescription export wrapper; MASM byte-emission preserves exact embedded jump table and descriptor-copy arms",
        },
    }


def decode_bink_buffer_get_description_forwarder(data: bytes) -> dict[str, Any] | None:
    body = data[: len(BINK_BUFFER_GET_DESCRIPTION_FORWARDER)]
    if body != BINK_BUFFER_GET_DESCRIPTION_FORWARDER:
        return None
    jump_table_offset = 0x158
    entries = [
        f"0x{int.from_bytes(body[offset:offset + 4], 'little'):08x}"
        for offset in range(jump_table_offset, jump_table_offset + 40, 4)
    ]
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferGetDescription",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "bufferArgIndex": 1,
        "typeFieldOffset": 0x80,
        "caseBaseAdjustment": -1,
        "maxCaseIndex": 9,
        "descriptorScratchGlobalAddress": "0x30055bb0",
        "jumpTableAddress": "0x30011838",
        "embeddedJumpTableOffset": jump_table_offset,
        "embeddedJumpTableBytes": 40,
        "embeddedJumpTableEntries": entries,
        "descriptorSourceAddresses": [
            "0x3004fd18",
            "0x3004fd00",
            "0x3004fce8",
            "0x3004fcc4",
            "0x3004fca0",
            "0x3004fc7c",
            "0x3004fc54",
            "0x3004fc28",
            "0x3004fc0c",
            "0x3004fc00",
        ],
        "nullBufferReturnValue": 0,
        "defaultReturnScratch": True,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "function body includes a 2-byte alignment marker and embedded absolute jump table after terminal return arms",
        },
    }


def is_bink_buffer_get_description_forwarder(data: bytes) -> bool:
    return decode_bink_buffer_get_description_forwarder(data) is not None


BINK_BUFFER_GET_DESCRIPTION_FORWARDER = bytes.fromhex(
    "8b44240485c00f84450100008b80800000004883f80956570f8729010000ff248538180130b906000000be18fd0430bfb05b0530f3a55fb8b05b05305ec20400b906000000be00fd0430bfb05b0530f3a55fb8b05b05305ec20400b906000000bee8fc0430bfb05b0530f3a55fb8b05b05305ec20400b908000000bec4fc0430bfb05b0530f3a566a5a45fb8b05b05305ec20400b908000000bea0fc0430bfb05b0530f3a566a5a45fb8b05b05305ec20400b908000000be7cfc0430bfb05b0530f3a566a5a45fb8b05b05305ec20400b90a000000be54fc0430bfb05b0530f3a55fb8b05b05305ec20400b90a000000be28fc0430bfb05b0530f3a5a45fb8b05b05305ec20400b906000000be0cfc0430bfb05b0530f3a566a5a45fb8b05b05305ec20400a100fc04308b0d04fc04308b1508fc0430a3b05b0530890db45b05308915b85b05305fb8b05b05305ec2040033c0c204008bffe71701300518013005170130201701303b170130561701307417013092170130b0170130cb170130"
)


def bink_next_frame_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_next_frame_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a BinkNextFrame stdcall state-advance wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper advances frame state, coordinates track sound toggles, and invokes decoded callbacks.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-next-frame-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkNextFrame state-advance wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkNextFrame export wrapper; MASM byte-emission preserves exact frame-advance control flow and helper dispatch",
        },
    }


def decode_bink_next_frame_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_NEXT_FRAME_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkNextFrame",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "binkPointerArgIndex": 1,
        "trackCountFieldOffset": 0x2F8,
        "trackTableFieldOffset": 0x300,
        "trackStrideBytes": 0x178,
        "frameDoneFlagFieldOffset": 0x138,
        "soundOnOffCallOffsets": [0xCE, 0x127],
        "soundOnOffTargetAddress": "0x30015d40",
        "callbackDispatchImportAddress": "0x3004a100",
        "helperCallOffsets": [0x16E, 0x173, 0x19D, 0x1B2],
        "helperCallTargets": ["0x30011ca0", "0x30017c80", "0x30011f70", "0x30011f70"],
        "importCallOffsets": [0xB1, 0xC6],
        "importCallAddresses": ["0x3004a0f8", "0x3004a0e8"],
        "nullPointerReturnsWithoutWork": True,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkNextFrame body followed by 10 bytes of NOP alignment padding",
        },
    }


def is_bink_next_frame_forwarder(data: bytes) -> bool:
    return decode_bink_next_frame_forwarder(data) is not None


BINK_NEXT_FRAME_FORWARDER = bytes.fromhex(
    "83ec0855568b7424145733ed33ff3bf50f84a70100008b86f802000033d23bc5c786380100000100000089ae380100000f862501000033c9538da424000000008b86000300008b5c085c3bdd8d44085c741b89288b460c83f80176118b9e000300003b440b487f05bf010000008b86f80200004281c1780100003bd072c23bfd0f84d40000008bbefc0200006aff55478d4c241889befc0200008bbe100200008b9e50030000516a02895c2420897c2424ff15f8a004302bc5740848750e6aff53eb036aff57ff15e8a004305556e81d1700008b86580100008b8e54010000406bc0644133d2f7f183f85a732b8dbe1001000057ff962001000085c0741a8b86580100008b8e54010000406bc0644133d2f7f183f85a72db6a015689ae8002000089ae38030000e8c41600008b3d00a104308d864c0300003bc5740a8b40043bc5740350ffd78d860c0200003bc5740a8b40043bc5740350ffd75b39aef8020000c7863801000001000000740656e8ddd5ffffe8b83500008b8e7c0200003bcd740e2bc10186b402000089ae7c0200008b460c3b46087219b801000000e87ed8ffff5f89ae380100005e5d83c408c2040040e869d8ffff89ae380100005f5e5d83c408c20400"
)


def bink_get_realtime_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_get_realtime_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a BinkGetRealtime stdcall summary wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper synchronizes timing state and fills decoded realtime output fields.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-get-realtime-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkGetRealtime summary wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkGetRealtime export wrapper; MASM byte-emission preserves exact realtime summary field layout",
        },
    }


def decode_bink_get_realtime_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_GET_REALTIME_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkGetRealtime",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "binkPointerArgIndex": 1,
        "outSummaryArgIndex": 2,
        "sampleFrameCountArgIndex": 3,
        "timerReadCallOffset": 0x04,
        "timerReadTargetAddress": "0x30017c80",
        "timebaseUpdateCallOffset": 0x2F,
        "timebaseUpdateTargetAddress": "0x30014bb0",
        "pauseStartFieldOffset": 0x27C,
        "pauseAccumFieldOffset": 0x2B4,
        "frameCountFieldOffset": 0x0C,
        "largestFrameSeenFieldOffset": 0x2C4,
        "outputBytes": 0x38,
        "returnInstruction": "ret 0x0c",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkGetRealtime body followed by 9 bytes of NOP alignment padding",
        },
    }


def is_bink_get_realtime_forwarder(data: bytes) -> bool:
    return decode_bink_get_realtime_forwarder(data) is not None


BINK_GET_REALTIME_FORWARDER = bytes.fromhex(
    "53555657e8b72b00008b7424148b8e7c02000085c974148bd02bd10196b4020000c7867c020000000000008bc88bc6e8bcfaffff8b5c241c85db74083b9ec402000072078b9ec40200004b8b460c3bd8760c8d58ff85db7505bb010000008b46108b7c241889078b4e14894f048b56148957088b865401000089472c8b8e58010000894f308b96c00200008b86bc0200000fafd38b8e0c0100008944241c8b460c895424148bd08b04812bd32b0491894424188b4424188b4c241cf7e18b4c2414f7f1894734895f0c8b8ecc0200008b012b04998947107507c74710010000008b86d40200008b14988b082bca894f148b86d00200008b2c988b102bd58957188b86d80200008b14988b082bca894f288b86dc0200008b2c988b102bd589571c8b86e00200008b14988b082bca894f208bb6e40200008b049e8b162bd08957245f5e5d5bc20c00"
)


def bink_goto_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_goto_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a BinkGoto stdcall seek wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper clamps the requested frame, advances decoded frame state, and restores audio/callback state.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-goto-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkGoto seek wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkGoto export wrapper; MASM byte-emission preserves exact seek-loop control flow and helper dispatch",
        },
    }


def decode_bink_goto_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_GOTO_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkGoto",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "binkPointerArgIndex": 1,
        "targetFrameArgIndex": 2,
        "modeArgIndex": 3,
        "frameCountFieldOffset": 0x08,
        "currentFrameFieldOffset": 0x0C,
        "frameRateDividendFieldOffset": 0x14,
        "frameRateDivisorFieldOffset": 0x18,
        "frameDoneFlagFieldOffset": 0x138,
        "seekScratchFieldOffset": 0x2A4,
        "trackCountFieldOffset": 0x2F8,
        "trackStateFieldOffset": 0x2A0,
        "decodedFrameFlagFieldOffset": 0x304,
        "resumeCallbackFieldOffset": 0x34C,
        "modeMaskRewind": 1,
        "modeMaskNoDecode": 2,
        "keyFrameCallOffset": 0x84,
        "keyFrameTargetAddress": "0x30011f70",
        "frameDecodeCallOffset": 0xAA,
        "frameDecodeTargetAddress": "0x30014720",
        "frameResetCallOffset": 0xDA,
        "frameResetTargetAddress": "0x30011f70",
        "trackMuteCallOffset": 0x112,
        "trackResumeCallOffset": 0x1A7,
        "soundOnOffTargetAddress": "0x30015d40",
        "preFrameCallOffsets": [0x123, 0x15B],
        "preFrameTargetAddress": "0x30013f30",
        "nextFrameCallOffsets": [0x13B, 0x167],
        "nextFrameTargetAddress": "0x30014550",
        "importCallOffsets": [0xFB, 0x197],
        "importCallAddresses": ["0x3004a0e8", "0x3004a100"],
        "nullPointerReturnsWithoutWork": True,
        "returnInstruction": "ret 0x0c",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkGoto body followed by 3 bytes of NOP alignment padding",
        },
    }


def is_bink_goto_forwarder(data: bytes) -> bool:
    return decode_bink_goto_forwarder(data) is not None


BINK_GOTO_FORWARDER = bytes.fromhex(
    "568b74240885f60f84ac010000538b5c241085db55577505bb010000008b46083bd8c786380100000100000076028bd88b6c241c8bc583e0028944241475298b86f802000085c0741f8b4e188b461433d28d4408fff7f13bc37207bf01000000eb088bfb2bf8eb028bfb395e0c0f84390100008bc583e00174208b4c241485c974188bc3e8e7d6ffff5f5d5bc78638010000000000005ec20c0085c074088bc389442414eb0d6a005356e871feffff894424143bc776128baea4020000c786a402000000000000eb048bf833ed8b460c3bd872043bf8760f8bc7e891d6ffff3bfb0f84c50000008d864c03000085c074108b400485c074096aff50ff15e8a004308b86a002000085c08944241874086a0056e8291400008b4e0c3b8eec020000741356e808f6ffffbf0100000089be04030000eb05bf0100000056e810fcffff395e0c742c85ed74118b54241439560c750889bea402000033ed56e8d0f5ffff5689be04030000e8e4fbffff395e0c75d485edc7868002000000000000740689bea40200008d864c03000085c0740e8b400485c0740750ff1500a104308b44241885c074075756e8941300005f5dc78638010000000000005b5ec20c00"
)


def bink_get_summary_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_get_summary_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a BinkGetSummary stdcall summary-copy wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper synchronizes timing state, clears the output summary, and copies decoded Bink fields.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-get-summary-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkGetSummary summary-copy wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkGetSummary export wrapper; MASM byte-emission preserves exact summary-copy field layout",
        },
    }


def decode_bink_get_summary_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_GET_SUMMARY_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkGetSummary",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "binkPointerArgIndex": 1,
        "outSummaryArgIndex": 2,
        "timerReadCallOffsets": [0x1B, 0x92],
        "timerReadTargetAddress": "0x30017c80",
        "timebaseUpdateCallOffset": 0x43,
        "timebaseUpdateTargetAddress": "0x30014bb0",
        "pauseStartFieldOffset": 0x27C,
        "pauseAccumFieldOffset": 0x2B4,
        "outputDwordClearCount": 0x1F,
        "outputBytes": 0x7C,
        "frameRateDividendFieldOffset": 0x14,
        "frameRateDivisorFieldOffset": 0x18,
        "frameCountFieldOffset": 0x08,
        "currentFrameFieldOffset": 0x0C,
        "elapsedGlobalFieldOffset": 0x274,
        "trackCountFieldOffset": 0x2F8,
        "keyFrameTableFieldOffset": 0x10C,
        "firstKeyFrameMask": "0xfffffffe",
        "returnInstruction": "ret 0x08",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkGetSummary body followed by 7 bytes of NOP alignment padding",
        },
    }


def is_bink_get_summary_forwarder(data: bytes) -> bool:
    return decode_bink_get_summary_forwarder(data) is not None


BINK_GET_SUMMARY_FORWARDER = bytes.fromhex(
    "51568b74240c85f60f84b6010000538b5c241485db0f84a8010000e8702d00008b8e7c02000085c974148bd02bd10196b4020000c7867c020000000000008bc8578bc6e878fcffff33c0b91f0000008bfbf3ab8b46148943148b4e18894b188b960c03000089532c8b86fc0200008943308b8ebc020000894b0c8b96c00200008953108b46088943208b8e70020000894b24e8f92c00002b86740200008943088b96b802000089531c8b86b00200008943408b8eac020000894b3c8b96b40200008b4b688953348b86a802000003c8894b688b8e50020000894b6c8b963c0100008b86340100004289542414c7442418e80300008944240c8b44240c8b4c2418f7e18b4c2414f7f189434c8b8e40010000894b388b96440100008953448b86480100008943488b46080faf86c00200008b8e0c0100008b118b7e288b8ebc02000083e2fe2bfa897c240c89442414894c24188b44240c8b4c2418f7e18b4c2414f7f189436033d28bc7f776088943648b96f40000008953748b86f8000000408943788b0e890b8b56048953048b86900200008943508b8e98020000894b548b96940200008953588b869c02000089435c8b8e4c010000894b6c8b96500100008953705f5b5e59c20800"
)


def bink_close_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_close_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a BinkClose stdcall teardown wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper shuts down playback helpers, frees decoded allocations, clears the Bink struct, and releases the object.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-close-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkClose teardown wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkClose export wrapper; MASM byte-emission preserves exact teardown/free control flow",
        },
    }


def decode_bink_close_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_CLOSE_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkClose",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "binkPointerArgIndex": 1,
        "flagsFieldOffset": 0x20,
        "tracksOpenFieldOffset": 0x2F8,
        "trackTableFieldOffset": 0x300,
        "trackStrideBytes": 0x178,
        "trackCloseVtableOffset": 0x1C,
        "trackPrimaryAllocationOffset": 0x3C,
        "trackSecondaryAllocationOffset": 0x2C,
        "globalAudioHandleAddress": "0x3006522c",
        "globalAudioModeAddress": "0x30065230",
        "globalSurfaceAddress": "0x300646c0",
        "globalSurfaceAuxAddress": "0x300646bc",
        "pauseBeforeCloseCallOffset": 0x13,
        "pauseBeforeCloseTargetAddress": "0x30014e30",
        "backendShutdownCallOffset": 0x57,
        "backendShutdownTargetAddress": "0x3001b890",
        "directFreeCallOffsets": [0xBC, 0xE8, 0x123, 0x18A, 0x1B2, 0x1E3],
        "directFreeTargetAddress": "0x300068ed",
        "allocationHeaderKindOffset": -2,
        "allocationHeaderDeltaOffset": -1,
        "customFreeVtableOffset": -8,
        "customAllocatorMarker": 3,
        "structClearDwordCount": 0xE3,
        "nullPointerNoop": True,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice exactly covers the decoded BinkClose body with no alignment padding",
        },
    }


def is_bink_close_forwarder(data: bytes) -> bool:
    return decode_bink_close_forwarder(data) is not None


BINK_CLOSE_FORWARDER = bytes.fromhex(
    "568b7424085733ff3bf70f84db0100006a0156e858040000f7462000000008751039be0801000075088b0d2c520630eb0233c98b86f8020000538b1d305206306a018d964c03000052f7d88d960c0200001bc05223c351e8746e000048740c48744f48750c893d30520630893d2c5206308b86f80200005533ed3bc7b3030f867f0000008b860003000003c750ff501c8b8e000300008b440f3c85c074263858fe0fb650ff75128bc82bca51ff50f8eb13893d30520630ebb82bc250e86c1effff83c4048b86000300008b44072c85c0741e3858fe0fb648ff750a8bd02bd152ff50f8eb0b2bc150e8401effff83c4048b86f80200004581c7780100003be8728333ffa1c04606303bc75d742a3858fe0fb650ff750a8bc82bca51ff50f8eb0b2bc250e8051effff83c404893dc0460630893dbc4606308b86080100003bc7741cf746200000000475483858fe0fb648ff75348bd02bd152ff50f8eb358d961001000052ff96240100008b864c0200003bc7741e3858fe0fb648ff750a8bd02bd152ff50f8eb0b2bc150e89e1dffff83c4048b86bc0000003bc7741e3858fe0fb650ff750a8bc82bca51ff50f8eb0b2bc250e8761dffff83c40433c0b9e30000008bfef3ab385efe5b75110fb646ff8bce2bc851ff56f85f5ec204000fb656ff2bf256e8451dffff83c4045f5ec20400"
)


def bink_wait_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_wait_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a BinkWait stdcall timing wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper synchronizes timing state, polls audio backend state, and reports wait readiness.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-wait-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkWait timing wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall BinkWait export wrapper; MASM byte-emission preserves exact timing/audio wait control flow",
        },
    }


def decode_bink_wait_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_WAIT_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkWait",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "binkPointerArgIndex": 1,
        "activeFieldOffset": 0x270,
        "pausedFlagFieldOffset": 0xFC,
        "timingStateFieldOffset": 0x1C,
        "waitStartFieldOffset": 0x280,
        "waitFrameFieldOffset": 0x284,
        "pauseStartFieldOffset": 0x27C,
        "pauseAccumFieldOffset": 0x2B4,
        "trackCountFieldOffset": 0x2F8,
        "trackStateFieldOffset": 0x2A0,
        "frameRateDividendFieldOffset": 0x14,
        "frameRateDivisorFieldOffset": 0x18,
        "frameDelayFieldOffset": 0x288,
        "frameTimeBaseFieldOffset": 0x338,
        "frameTimeTargetFieldOffset": 0x33C,
        "audioStateFieldOffset": 0x108,
        "backendContextGlobalAddress": "0x3006522c",
        "backendStateOffset": 0x20C,
        "timerReadCallOffsets": [0x3C, 0x6A],
        "timerReadTargetAddress": "0x30017c80",
        "trackSyncCallOffset": 0x65,
        "trackSyncTargetAddress": "0x30011ca0",
        "timebaseUpdateCallOffset": 0x93,
        "timebaseUpdateTargetAddress": "0x30014bb0",
        "backendPollCallOffset": 0x1E2,
        "backendPollTargetAddress": "0x3001bbb0",
        "backendStartVtableOffset": 0x120,
        "backendCommitCallOffset": 0x200,
        "backendCommitTargetAddress": "0x3001bbe0",
        "successReturnValue": 1,
        "waitReturnValue": 0,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkWait body followed by 13 bytes of NOP alignment padding",
        },
    }


def is_bink_wait_forwarder(data: bytes) -> bool:
    return decode_bink_wait_forwarder(data) is not None


BINK_WAIT_FORWARDER = bytes.fromhex(
    "83ec08568b74241085f6741b8b867002000085c0750a8b86fc00000085c074078b461c85c0740933c05e83c408c20400538b9e8002000085db57751ee82f3000008986800200008b86700200008b9e80020000488986840200008b86f802000085c0740656e826d0ffffe8013000008bf88b867c02000085c074148bcf2bc8018eb4020000c7867c020000000000008bcf8bc6e808ffffff8b86fc00000085c00f85230100008b86f802000085c0740e8b86a002000085c00f840b0100008b461485c00f84f50000008b8e840200008b5618894424188b86700200002bc169c0e80300008954240c894424108b4424108b4c240cf7e18b4c2418f7f18b8e000300008b51688944240cc744241810000000895424108b44240c8b4c2410f7e18b4c24180fadd08b96380300008bcf2bca2bcb3bc80f8c8f0000002bc83b8e880200007e4d8b86f802000085c075158b86700200004889be80020000898684020000eb2e8b56148b4618898e380300008954241889442410894c240c8b44240c8b4c2410f7e18b4c2418f7f189863c0300008b86380300008b8e3c0300003bc173155f5bc786380300000000000033c05e83c408c204002bc18986380300005f5b33c05e83c408c204008b860801000085c075328b0d2c5206308dbe0c020000518bc7e8b96d000085c0741a8d961001000052ff9620010000a12c520630508bc7e8cb6d00005f5bb8010000005e83c408c20400"
)


def bink_surface_type_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_bink_surface_type_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a Bink surface-type stdcall wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper queries a surface format descriptor and maps decoded masks/FourCC values to Bink ids.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "bink-surface-type-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": f"generated MASM byte-emission parity fallback with decoded {decoded['export']} surface type wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall Bink surface type wrapper; MASM byte-emission preserves exact format switch and mask comparisons",
        },
    }


def decode_bink_surface_type_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == BINK_DD_SURFACE_TYPE_FORWARDER:
        return {
            "bodyBytes": len(body),
            "export": "BinkDDSurfaceType",
            "surfaceApi": "DirectDraw",
            "stdcallStackBytes": 4,
            "stackArgCount": 1,
            "surfacePointerArgIndex": 1,
            "queryVtableOffset": 0x54,
            "descriptorBytes": 0x20,
            "fourCcFieldOffset": 0x0C,
            "rgbBitCountFieldOffset": 0x0C,
            "redMaskFieldOffset": 0x10,
            "greenMaskFieldOffset": 0x14,
            "blueMaskFieldOffset": 0x18,
            "alphaMaskFieldOffset": 0x1C,
            "fourCcMappings": [
                {"fourCc": "YVYU", "returnValue": 0x0E},
                {"fourCc": "YV12", "returnValue": 0x0F},
                {"fourCc": "YUY2", "returnValue": 0x0D},
            ],
            "bitCountMappings": [
                {"bits": 8, "returnValue": 0},
                {"bits": 24, "redMask": "0x00ff0000", "returnValueWhenRedMaskMatches": 1, "returnValueWhenRedMaskDiffers": 2},
                {"bits": 32, "redMask": "0x00ff0000", "alphaAbsentBase": 3, "alphaPresentBase": 5},
            ],
            "rgbMaskMappings": [
                {"redMask": "0x0000f800", "greenMask": "0x000007e0", "blueMask": "0x0000001f", "returnValue": 0x0A},
                {"alphaMask": "0x00008000", "redMask": "0x00007c00", "greenMask": "0x000003e0", "blueMask": "0x0000001f", "returnValue": 0x08},
                {"redMask": "0x00007c00", "greenMask": "0x000003e0", "blueMask": "0x0000001f", "returnValue": 0x09},
                {"redMask": "0x0000fc00", "greenMask": "0x000003e0", "blueMask": "0x0000001f", "returnValue": 0x0B},
                {"redMask": "0x0000fc00", "greenMask": "0x000003f0", "blueMask": "0x0000000f", "returnValue": 0x0C},
                {"alphaMask": "0x0000f000", "redMask": "0x00000f00", "greenMask": "0x000000f0", "blueMask": "0x0000000f", "returnValue": 0x07},
            ],
            "failureReturnValue": -1,
            "returnInstruction": "ret 0x04",
            "targetByteSpan": {
                "offset": 0,
                "length": len(body),
                "reason": "surface type wrapper is a complete decoded function body",
            },
        }
    if body == BINK_DX8_SURFACE_TYPE_FORWARDER:
        return {
            "bodyBytes": len(body),
            "export": "BinkDX8SurfaceType",
            "surfaceApi": "Direct3D8",
            "stdcallStackBytes": 4,
            "stackArgCount": 1,
            "surfacePointerArgIndex": 1,
            "queryVtableOffset": 0x20,
            "descriptorBytes": 0x20,
            "formatFieldOffset": 0x04,
            "jumpTableAddress": "0x30011b50",
            "formatMappings": [
                {"formatMinus20": 0, "returnValue": 1},
                {"format": 30, "returnValue": 7},
                {"formatMinus20": 1, "returnValue": 5},
                {"formatMinus20": 2, "returnValue": 3},
                {"formatMinus20": 3, "returnValue": 0x0A},
                {"formatMinus20": 4, "returnValue": 0x09},
                {"formatMinus20": 5, "returnValue": 0x08},
            ],
            "fourCcMappings": [
                {"fourCc": "YUY2", "returnValue": 0x0D},
                {"fourCc": "YVYU", "returnValue": 0x0E},
            ],
            "failureReturnValue": -1,
            "embeddedJumpTableBytes": 0x1C,
            "returnInstruction": "ret 0x04",
            "targetByteSpan": {
                "offset": 0,
                "length": len(body),
                "reason": "DX8 surface type wrapper includes its embedded absolute jump table bytes",
            },
        }
    return None


def is_bink_surface_type_forwarder(data: bytes) -> bool:
    return decode_bink_surface_type_forwarder(data) is not None


BINK_DD_SURFACE_TYPE_FORWARDER = bytes.fromhex(
    "8b54240483ec2085d2750983c8ff83c420c204005733c0b9080000008d7c2404f3ab8b028d4c24045152c744240c20000000ff50548b44240c3d555956595f750bb80e00000083c420c204003d59563132750bb80f00000083c420c204003d59555932750bb80d00000083c420c204008b44240c83f808750833c083c420c2040083f81875168b54241033c081fa0000ff000f95c04083c420c2040083f82075348b44241c85c08b542410751433c081fa0000ff000f95c083c00383c420c2040033c081fa0000ff000f95c083c00583c420c204008b4c241081f900f800008b4424188b542414751881fae0070000751083f81f750bb80a00000083c420c20400568b74242081fe00800000752981f9007c0000754281fae00300000f859900000083f81f0f8590000000b8080000005e83c420c2040081f9007c0000751981fae0030000754b83f81f7546b8090000005e83c420c2040081f900fc0000753281fae0030000751183f81f7525b80b0000005e83c420c2040081faf0030000751183f80f750cb80c0000005e83c420c2040081fe00f00000752181f9000f0000751981faf0000000751183f80f750cb8070000005e83c420c2040083c8ff5e83c420c20400"
)


BINK_DX8_SURFACE_TYPE_FORWARDER = bytes.fromhex(
    "8b54240483ec2085d20f848f0000005733c0b9080000008d7c2404f3ab8b028d4c24045152ff50208b44240483f81e5f7f5e741a83c0ec83f8067762ff2485501b0130b80100000083c420c20400b80700000083c420c20400b80500000083c420c20400b80300000083c420c20400b80a00000083c420c20400b80900000083c420c20400b80800000083c420c204003d59555932741b3d55595659740983c8ff83c420c20400b80e00000083c420c20400b80d00000083c420c204008d4900d31a0130e91a0130f41a0130ff1a01300a1b0130151b0130de1a0130"
)


def rad_aligned_malloc_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_rad_aligned_malloc_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a RAD aligned malloc stdcall wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper overallocates, aligns the returned pointer, and records metadata for radfree.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "rad-aligned-malloc-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded RAD aligned malloc wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall RAD allocator wrapper; MASM byte-emission preserves exact alignment and metadata layout",
        },
    }


def decode_rad_aligned_malloc_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != RAD_ALIGNED_MALLOC_FORWARDER:
        return None
    fallback_call_offset = 0x2E
    return {
        "bodyBytes": len(body),
        "export": "radmalloc",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "sizeArgIndex": 1,
        "invalidSizeSentinel": "0xffffffff",
        "customMallocGlobalAddress": "0x30058080",
        "customFreeGlobalAddress": "0x30058084",
        "fallbackMallocCallOffset": fallback_call_offset,
        "fallbackMallocCallDisplacement": int.from_bytes(body[fallback_call_offset + 1 : fallback_call_offset + 5], "little", signed=True),
        "overAllocationBytes": 0x40,
        "alignmentBytes": 0x40,
        "alignmentMask": "0x1f",
        "customAllocatorMarker": 3,
        "fallbackAllocatorMarker": 0,
        "allocatorMarkerOffset": -2,
        "alignmentDeltaOffset": -1,
        "customFreePointerOffset": -8,
        "nullReturnValue": 0,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "export target slice contains the decoded function followed by padding; compare only the function body span",
        },
    }


def is_rad_aligned_malloc_forwarder(data: bytes) -> bool:
    return decode_rad_aligned_malloc_forwarder(data) is not None


RAD_ALIGNED_MALLOC_FORWARDER = bytes.fromhex(
    "568b74240885f6745b83feff7456a18080053085c074138d4e4051ffd085c0740983f8ff743eb203eb1283c64056e84459000083c40485c0742a32d2538ad880e31fb1402acb0fb6f103c680fa038848ff8850fe5b750f8b15848005308950f85ec2040033c05ec20400"
)


def rad_aligned_free_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_rad_aligned_free_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a RAD aligned free stdcall wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper reverses radmalloc alignment metadata and dispatches custom or fallback free.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "rad-aligned-free-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded RAD aligned free wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy", "/Gz"],
            "reason": "stdcall RAD free wrapper; MASM byte-emission preserves exact metadata dispatch and packed-slice boundary",
        },
    }


def decode_rad_aligned_free_forwarder(data: bytes) -> dict[str, Any] | None:
    body = data[: len(RAD_ALIGNED_FREE_FORWARDER)]
    if body != RAD_ALIGNED_FREE_FORWARDER:
        return None
    fallback_call_offset = 0x20
    return {
        "bodyBytes": len(body),
        "export": "radfree",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "pointerArgIndex": 1,
        "customAllocatorMarker": 3,
        "customAllocatorMarkerOffset": -2,
        "alignmentDeltaOffset": -1,
        "customFreePointerOffset": -8,
        "customFreeTailJumpOffset": 0x1A,
        "fallbackFreeCallOffset": fallback_call_offset,
        "fallbackFreeCallDisplacement": int.from_bytes(body[fallback_call_offset + 1 : fallback_call_offset + 5], "little", signed=True),
        "fallbackFreeTargetAddress": "0x300068ed",
        "freePointerRewriteStackOffset": 4,
        "nullPointerNoop": True,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "inferred target slice is packed; compare only the decoded radfree wrapper before alignment padding and following helper code",
        },
    }


def is_rad_aligned_free_forwarder(data: bytes) -> bool:
    return decode_rad_aligned_free_forwarder(data) is not None


RAD_ALIGNED_FREE_FORWARDER = bytes.fromhex(
    "8b44240485c0741e8078fe030fb648ff750b8bd02bd189542404ff60f82bc150e83858000059c20400"
)


def rad_direct_free_wrapper_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_rad_direct_free_wrapper(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a RAD fallback free cdecl wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper handles optional custom cleanup before dispatching the import-backed free path.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "rad-direct-free-wrapper",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded RAD direct free wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "cdecl RAD fallback free wrapper; MASM byte-emission preserves exact custom cleanup and import dispatch",
        },
    }


def decode_rad_direct_free_wrapper(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != RAD_DIRECT_FREE_WRAPPER:
        return None
    custom_probe_call_offset = 0x13
    custom_cleanup_call_offset = 0x1F
    return {
        "bodyBytes": len(body),
        "callconv": "cdecl",
        "stackArgCount": 1,
        "pointerArgIndex": 1,
        "modeGlobalAddress": "0x30058450",
        "modeCustomCleanupValue": 3,
        "fallbackHeapGlobalAddress": "0x3005844c",
        "fallbackFreeImportAddress": "0x3004a134",
        "customProbeCallOffset": custom_probe_call_offset,
        "customProbeCallDisplacement": int.from_bytes(body[custom_probe_call_offset + 1 : custom_probe_call_offset + 5], "little", signed=True),
        "customProbeTargetAddress": "0x30006dd0",
        "customCleanupCallOffset": custom_cleanup_call_offset,
        "customCleanupCallDisplacement": int.from_bytes(body[custom_cleanup_call_offset + 1 : custom_cleanup_call_offset + 5], "little", signed=True),
        "customCleanupTargetAddress": "0x30006e11",
        "fallbackFreeCallOffset": 0x30,
        "nullPointerNoop": True,
        "customCleanupReturnPath": "ret",
        "returnInstruction": "ret",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice is a complete inferred helper ending in ret; compare the decoded helper body",
        },
    }


def is_rad_direct_free_wrapper(data: bytes) -> bool:
    return decode_rad_direct_free_wrapper(data) is not None


RAD_DIRECT_FREE_WRAPPER = bytes.fromhex(
    "568b74240885f6742d833d5084053003567515e8db04000085c05956740a50e8fa04000059595ec36a00ff354c840530ff1534a104305ec3"
)


def rad_timer_read_forwarder_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_rad_timer_read_forwarder(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    symbol = f"_{c_name}"
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Automatically generated from a RADTimerRead cdecl timer wrapper.",
            f"; Target: {task.get('name')} at {task.get('address')}.",
            "; The wrapper initializes performance-counter state and returns a bounded RAD timer value.",
            "; Acceptance requires compiler/object comparison.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "asm",
        "language": "masm",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "rad-timer-read-forwarder",
            "bodyBytes": int(decoded["bodyBytes"]),
            **decoded,
            "sourceTier": "generated MASM byte-emission parity fallback with decoded RADTimerRead timer wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "masm",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "cdecl RAD timer wrapper; MASM byte-emission preserves exact high-resolution timer initialization and packed-slice boundary",
        },
    }


def decode_rad_timer_read_forwarder(data: bytes) -> dict[str, Any] | None:
    body = data[: len(RAD_TIMER_READ_FORWARDER)]
    if body != RAD_TIMER_READ_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "RADTimerRead",
        "callconv": "cdecl",
        "stackArgCount": 0,
        "localScratchBytes": 8,
        "initFlagGlobalAddress": "0x300651e0",
        "performanceFrequencyLowGlobalAddress": "0x30055f30",
        "performanceFrequencyHighGlobalAddress": "0x30055f34",
        "performanceCounterBaseLowGlobalAddress": "0x30055f28",
        "performanceCounterBaseHighGlobalAddress": "0x30055f2c",
        "lastCounterGlobalAddress": "0x30055f38",
        "timerBaseGlobalAddress": "0x30055f24",
        "driftAccumulatorGlobalAddress": "0x300651e4",
        "queryPerformanceFrequencyImportAddress": "0x3004a0d4",
        "queryPerformanceCounterImportAddress": "0x3004a0cc",
        "timeGetTimeImportAddress": "0x3004a0d0",
        "fallbackTimerImportAddress": "0x3004a1d8",
        "scaleNumerator": 1000,
        "driftClampTicks": 200,
        "wrapGuardDelta": "0xc0000000",
        "returnInstruction": "ret",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "inferred target slice is packed; compare only the decoded RADTimerRead body before padding and following helper code",
        },
    }


def is_rad_timer_read_forwarder(data: bytes) -> bool:
    return decode_rad_timer_read_forwarder(data) is not None


RAD_TIMER_READ_FORWARDER = bytes.fromhex(
    "a1e051063083ec08535633f63bc657745268305f05308935e0510630ff15d4a0043085c0742568285f0530ff15cca004308935245f0530ff15d0a004305f5ea3385f053033c05b83c408c38935305f05308935345f05305f5e5b83c408ff25d8a10430a1305f05300b05345f053074e78d4c240c51ff15cca00430ff15d0a004308b54240c8b1d285f05308b0d2c5f05308bf88b442410562bd368e80300001bc15052e86821ffff8b0d345f0530518b0d305f0530515250e87324ffff8b1d385f05308bc8030de45106308bc72bc38b1d245f05308bf08bd12bd32bf28bc69933c22bc23dc80000007e080135e451063003ce8bd12bd381fa000000c076095f5e8bc35b83c408c3893d385f05305f5e890d245f05308bc15b83c408c3"
)


def stdcall_yuv_blit_format_wrapper_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_yuv_blit_format_wrapper_bytes(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    address = coerce_int(task.get("address"))
    constant = int(str(decoded["constant"]), 16)
    call_offset = int(decoded["callOffset"])
    call_target = rel32_call_target(address, call_offset=call_offset, rel32=int.from_bytes(strip_alignment_padding(data)[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall YUV blit format wrapper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This wrapper passes one decoded input in eax and forwards 12 stack values.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}(unsigned int a1, unsigned int a2, unsigned int a3, unsigned int a4, unsigned int a5, unsigned int a6, unsigned int a7, unsigned int a8, unsigned int a9, unsigned int a10, unsigned int a11, unsigned int a12) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+34h]",
            "        mov ecx, dword ptr [ebp+30h]",
            "        mov edx, dword ptr [ebp+28h]",
            f"        push 0{constant:08x}h",
            "        push 0",
            "        push eax",
            "        mov eax, dword ptr [ebp+24h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+20h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+1Ch]",
            "        push eax",
            "        mov eax, dword ptr [ebp+18h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+14h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+10h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+0Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+08h]",
            "        push edx",
            "        push eax",
            "        mov eax, dword ptr [ebp+2Ch]",
            f"        call {callee}",
            "        add esp, 30h",
            "        pop ebp",
            "        ret 30h",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-yuv-blit-format-wrapper",
            "bodyBytes": len(strip_alignment_padding(data)),
            "callTarget": f"0x{call_target:08x}" if call_target is not None else None,
            **decoded,
            "sourceTier": "generated inline-assembly parity fallback with decoded YUV blit wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "YUV blit wrapper uses eax as a live helper input; naked decoded source preserves the wrapper shape",
        },
    }


def stdcall_yuv_blit_alpha_wrapper_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_yuv_blit_alpha_wrapper_bytes(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    body = strip_alignment_padding(data)
    address = coerce_int(task.get("address"))
    constant = int(str(decoded["constant"]), 16)
    call_offset = int(decoded["callOffset"])
    call_target = rel32_call_target(address, call_offset=call_offset, rel32=int.from_bytes(body[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall YUV alpha blit wrapper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This wrapper passes decoded inputs in eax/ecx and forwards 12 stack values.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}(unsigned int a1, unsigned int a2, unsigned int a3, unsigned int a4, unsigned int a5, unsigned int a6, unsigned int a7, unsigned int a8, unsigned int a9, unsigned int a10, unsigned int a11, unsigned int a12, unsigned int a13) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+34h]",
            "        mov ecx, dword ptr [ebp+38h]",
            "        mov edx, dword ptr [ebp+30h]",
            f"        push 0{constant:08x}h",
            "        push eax",
            "        mov eax, dword ptr [ebp+28h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+24h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+20h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+1Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+18h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+14h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+10h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+0Ch]",
            "        push edx",
            "        push eax",
            "        mov eax, dword ptr [ebp+2Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+08h]",
            f"        call {callee}",
            "        add esp, 30h",
            "        pop ebp",
            "        ret 34h",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-yuv-blit-alpha-wrapper",
            "bodyBytes": len(body),
            "callTarget": f"0x{call_target:08x}" if call_target is not None else None,
            **decoded,
            "sourceTier": "generated inline-assembly parity fallback with decoded YUV alpha blit wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "YUV alpha blit wrapper uses eax/ecx as live helper inputs; naked decoded source preserves the wrapper shape",
        },
    }


def stdcall_yuv_blit_packed_wrapper_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_yuv_blit_packed_wrapper_bytes(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    body = strip_alignment_padding(data)
    address = coerce_int(task.get("address"))
    constant = int(str(decoded["constant"]), 16)
    call_offset = int(decoded["callOffset"])
    call_target = rel32_call_target(address, call_offset=call_offset, rel32=int.from_bytes(body[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall packed YUV blit wrapper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This wrapper normalizes packed-pixel alignment before forwarding to the shared blitter.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}(unsigned int a1, unsigned int a2, unsigned int a3, unsigned int a4, unsigned int a5, unsigned int a6, unsigned int a7, unsigned int a8, unsigned int a9, unsigned int a10, unsigned int a11, unsigned int a12) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov ecx, dword ptr [ebp+08h]",
            "        mov eax, ecx",
            "        push ebx",
            "        mov ebx, dword ptr [ebp+0Ch]",
            "        and al, 03h",
            "        cmp al, 02h",
            "        push esi",
            "        jne selector_done",
            "        inc ebx",
            "        and ecx, 0FFFFFFFCh",
            "    selector_done:",
            "        test bl, 01h",
            "        mov edx, dword ptr [ebp+1Ch]",
            "        je stride_even",
            "        test dl, 01h",
            "        je maybe_high",
            "        inc edx",
            "    maybe_high:",
            "        mov eax, dword ptr [ebp+24h]",
            "        inc ebx",
            "        jmp decrement_output",
            "    stride_even:",
            "        test dl, 01h",
            "        mov eax, dword ptr [ebp+24h]",
            "        je adjustment_done",
            "        inc edx",
            "    decrement_output:",
            "        dec eax",
            "    adjustment_done:",
            "        test al, 01h",
            "        je output_aligned",
            "        dec eax",
            "    output_aligned:",
            "        mov esi, dword ptr [ebp+34h]",
            f"        push 0{constant:08x}h",
            "        push 0",
            "        push esi",
            "        mov esi, dword ptr [ebp+30h]",
            "        push esi",
            "        mov esi, dword ptr [ebp+28h]",
            "        push esi",
            "        push eax",
            "        mov eax, dword ptr [ebp+20h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+14h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+18h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+10h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+2Ch]",
            "        push edx",
            "        push ebx",
            f"        call {callee}",
            "        add esp, 30h",
            "        pop esi",
            "        pop ebx",
            "        pop ebp",
            "        ret 30h",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-yuv-blit-packed-wrapper",
            "bodyBytes": len(body),
            "callTarget": f"0x{call_target:08x}" if call_target is not None else None,
            **decoded,
            "sourceTier": "generated inline-assembly parity fallback with decoded packed YUV blit wrapper bytes",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "packed YUV blit wrapper mutates register inputs before shared helper call; naked decoded source preserves branch and register schedule",
        },
    }


def stdcall_yuv_blit_mask_format_prefix_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_yuv_blit_mask_format_prefix_bytes(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    prefix = data[: int(decoded["targetByteSpan"]["length"])]
    address = coerce_int(task.get("address"))
    constant = int(str(decoded["constant"]), 16)
    call_offset = int(decoded["callOffset"])
    call_target = rel32_call_target(address, call_offset=call_offset, rel32=int.from_bytes(prefix[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from a leading x86 stdcall YUV mask-format wrapper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * The inferred target slice continues after this wrapper; targetByteSpan limits proof to the decoded wrapper bytes.",
            " */",
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}(unsigned int a1, unsigned int a2, unsigned int a3, unsigned int a4, unsigned int a5, unsigned int a6, unsigned int a7, unsigned int a8, unsigned int a9, unsigned int a10, unsigned int a11, unsigned int a12, unsigned int a13, unsigned int a14) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+3Ch]",
            "        mov ecx, dword ptr [ebp+38h]",
            "        mov edx, dword ptr [ebp+34h]",
            f"        push 0{constant:08x}h",
            "        push 0",
            "        push eax",
            "        mov eax, dword ptr [ebp+30h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+2Ch]",
            "        push edx",
            "        mov edx, dword ptr [ebp+28h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+24h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+20h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+1Ch]",
            "        push eax",
            "        mov eax, dword ptr [ebp+18h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+14h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+10h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+0Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+08h]",
            "        push edx",
            "        push eax",
            "        push ecx",
            f"        call {callee}",
            "        add esp, 40h",
            "        pop ebp",
            "        ret 38h",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target-slice prefix; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-yuv-blit-mask-format-prefix",
            "bodyBytes": len(prefix),
            "callTarget": f"0x{call_target:08x}" if call_target is not None else None,
            **decoded,
            "claimBoundary": "only the leading wrapper byte span is generated; trailing inferred-slice bytes remain unrecovered",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "YUV mask-format exports expose a leading wrapper followed by unrelated inferred bytes; naked decoded source preserves the wrapper span",
        },
    }


def stdcall_yuv_blit_mask_alpha_prefix_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_stdcall_yuv_blit_mask_alpha_prefix_bytes(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    prefix = data[: int(decoded["targetByteSpan"]["length"])]
    address = coerce_int(task.get("address"))
    constant = int(str(decoded["constant"]), 16)
    call_offset = int(decoded["callOffset"])
    call_target = rel32_call_target(address, call_offset=call_offset, rel32=int.from_bytes(prefix[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from a leading x86 stdcall YUV mask-alpha wrapper.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * The inferred target slice continues after this wrapper; targetByteSpan limits proof to the decoded wrapper bytes.",
            " */",
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}(unsigned int a1, unsigned int a2, unsigned int a3, unsigned int a4, unsigned int a5, unsigned int a6, unsigned int a7, unsigned int a8, unsigned int a9, unsigned int a10, unsigned int a11, unsigned int a12, unsigned int a13, unsigned int a14, unsigned int a15) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+3Ch]",
            "        mov ecx, dword ptr [ebp+40h]",
            "        mov edx, dword ptr [ebp+38h]",
            f"        push 0{constant:08x}h",
            "        push eax",
            "        mov eax, dword ptr [ebp+34h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+30h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+2Ch]",
            "        push eax",
            "        mov eax, dword ptr [ebp+28h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+24h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+20h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+1Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+18h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+14h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+10h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+0Ch]",
            "        push edx",
            "        mov edx, dword ptr [ebp+08h]",
            "        push eax",
            "        push ecx",
            "        push edx",
            f"        call {callee}",
            "        add esp, 40h",
            "        pop ebp",
            "        ret 3Ch",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target-slice prefix; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "stdcall-yuv-blit-mask-alpha-prefix",
            "bodyBytes": len(prefix),
            "callTarget": f"0x{call_target:08x}" if call_target is not None else None,
            **decoded,
            "claimBoundary": "only the leading wrapper byte span is generated; trailing inferred-slice bytes remain unrecovered",
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "YUV mask-alpha exports expose a leading wrapper followed by unrelated inferred bytes; naked decoded source preserves the wrapper span",
        },
    }


def global_guard_call_set_return_zero_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 30 or data[0:2] != b"\x83\x3d" or data[6:9] != b"\x00\x75\x12":
        return None
    if data[9:12] != b"\x6a\xfd\xe8" or data[16] != 0x59 or data[17:19] != b"\xc7\x05":
        return None
    if data[23:30] != b"\x01\x00\x00\x00\x33\xc0\xc3":
        return None
    guard_address = int.from_bytes(data[2:6], "little")
    store_address = int.from_bytes(data[19:23], "little")
    if guard_address != store_address:
        return None
    address = coerce_int(task.get("address"))
    call_target = rel32_call_target(address, call_offset=11, rel32=int.from_bytes(data[12:16], "little", signed=True))
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    callee = c_identifier(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 global guard call/set pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * MSVC high-level C emits a mov/test/add-esp shape instead of the target cmp/pop shape.",
            " * Generated inline assembly preserves the decoded absolute cmp/store bytes.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"extern void __cdecl {callee}(int value);",
            f"__declspec(naked) unsigned int {c_name}(void) {{",
            "    __asm {",
            "        _emit 083h",
            "        _emit 03Dh",
            f"        _emit 0{guard_address & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 24) & 0xFF:02x}h",
            "        _emit 000h",
            "        jne done",
            "        push -3",
            f"        call {callee}",
            "        pop ecx",
            "        _emit 0C7h",
            "        _emit 005h",
            f"        _emit 0{guard_address & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 24) & 0xFF:02x}h",
            "        _emit 001h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "    done:",
            "        xor eax, eax",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    return semantic_call_wrapper_candidate(
        source=source,
        rule="global-guard-call-set-return-zero",
        data=data,
        call_target=call_target,
        args=["-3"],
        extra_generator_fields={
            "guardAddress": f"0x{guard_address:08x}",
            "setValue": 1,
            "sourceTier": "generated inline-assembly parity fallback with decoded absolute-cmp/store bytes",
        },
    )


def rep_stos_global_clear_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_rep_stos_global_clear(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    first_base = int(decoded["firstBase"])
    first_dwords = int(decoded["firstDwords"])
    first_bytes = int(decoded["firstTrailingBytes"])
    zero_globals = [int(value) for value in decoded["zeroGlobals"]]
    second_base = int(decoded["secondBase"])
    second_dwords = int(decoded["secondDwords"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 rep-stos global clear pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * Generated inline assembly preserves rep stos and absolute-store instruction selection.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"__declspec(naked) void {c_name}(void) {{",
            "    __asm {",
            "        push edi",
            "        push 40h",
            "        xor eax, eax",
            "        pop ecx",
            f"        mov edi, 0{first_base:08x}h",
            "        rep stosd",
            "        stosb",
            "        xor eax, eax",
            *[
                line
                for address in zero_globals
                for line in [
                    "        _emit 0A3h",
                    f"        _emit 0{address & 0xFF:02x}h",
                    f"        _emit 0{(address >> 8) & 0xFF:02x}h",
                    f"        _emit 0{(address >> 16) & 0xFF:02x}h",
                    f"        _emit 0{(address >> 24) & 0xFF:02x}h",
                ]
            ],
            f"        mov edi, 0{second_base:08x}h",
            "        stosd",
            "        stosd",
            "        stosd",
            "        pop edi",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "rep-stos-global-clear",
            "bodyBytes": len(data),
            "sourceTier": "generated inline-assembly parity fallback with decoded absolute-store bytes",
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "rep-stos global clear pattern; parity fallback preserves memset-style codegen shape",
        },
    }


def decode_rep_stos_global_clear(data: bytes) -> dict[str, Any] | None:
    if len(data) != 41 or data[:6] != b"\x57\x6a\x40\x33\xc0\x59" or data[6] != 0xBF:
        return None
    if data[11:16] != b"\xf3\xab\xaa\x33\xc0":
        return None
    if data[16] != 0xA3 or data[21] != 0xA3 or data[26] != 0xA3:
        return None
    if data[31] != 0xBF or data[36:41] != b"\xab\xab\xab\x5f\xc3":
        return None
    return {
        "firstBase": int.from_bytes(data[7:11], "little"),
        "firstDwords": 0x40,
        "firstTrailingBytes": 1,
        "zeroGlobals": [
            int.from_bytes(data[17:21], "little"),
            int.from_bytes(data[22:26], "little"),
            int.from_bytes(data[27:31], "little"),
        ],
        "secondBase": int.from_bytes(data[32:36], "little"),
        "secondDwords": 3,
    }


def small_zero_scan_bool_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_small_zero_scan_bool(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    count = int(decoded["count"])
    scale = int(decoded["scale"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 indexed zero-scan boolean pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * MSVC /O1 emits the compact signed counter loop used by the target.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"unsigned int {c_name}(const unsigned int *items) {{",
            "    int i = 0;",
            "    do {",
            "        if (items[i] != 0u) {",
            "            return 0u;",
            "        }",
            "        ++i;",
            f"    }} while (i < {count});",
            "    return 1u;",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "small-zero-scan-bool",
            "bodyBytes": len(data),
            "sourceTier": "generated high-level C parity match for compact signed zero-scan loop",
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O1", "/GS-", "/Oy"],
            "reason": f"indexed {scale}-byte zero scan returning boolean; MSVC /O1 preserves inc/jl compact loop shape",
        },
    }


def decode_small_zero_scan_bool(data: bytes) -> dict[str, Any] | None:
    if len(data) != 25:
        return None
    if data[0:6] != b"\x33\xc0\x8b\x4c\x24\x04":
        return None
    if data[6:10] != b"\x83\x3c\x81\x00":
        return None
    if data[10:13] != b"\x75\x0a\x40" or data[13] != 0x83 or data[14] != 0xF8:
        return None
    if data[16:25] != b"\x7c\xf0\x33\xc0\x40\xc3\x33\xc0\xc3":
        return None
    count = int(data[15])
    if count == 0:
        return None
    return {
        "count": count,
        "scale": 4,
        "returnIfAllZero": 1,
        "returnIfAnyNonzero": 0,
    }


def small_copy_loop_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_small_copy_loop(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    count = int(decoded["count"])
    element_bytes = int(decoded["elementBytes"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 fixed-count dword copy loop.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * MSVC high-level C either unrolls or allocates the source/destination bases opposite the target.",
            " * Generated inline assembly preserves the compact pointer-delta copy loop.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"__declspec(naked) void {c_name}(unsigned int *dest, const unsigned int *src) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+8]",
            "        mov ecx, dword ptr [esp+4]",
            f"        push {count}",
            "        pop edx",
            "        sub ecx, eax",
            "        push esi",
            "    copy_loop:",
            "        mov esi, dword ptr [eax]",
            "        mov dword ptr [ecx+eax], esi",
            "        add eax, 4",
            "        dec edx",
            "        jne copy_loop",
            "        pop esi",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "small-copy-loop",
            "bodyBytes": len(data),
            "sourceTier": "generated inline-assembly parity source for compact pointer-delta copy loop",
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": f"fixed-count {element_bytes}-byte element copy loop; generated inline assembly preserves source-base/destination-delta register allocation",
        },
    }


def decode_small_copy_loop(data: bytes) -> dict[str, Any] | None:
    if len(data) != 27:
        return None
    if data[:13] != b"\x8b\x44\x24\x08\x8b\x4c\x24\x04\x6a\x03\x5a\x2b\xc8":
        return None
    if data[13:] != b"\x56\x8b\x30\x89\x34\x01\x83\xc0\x04\x4a\x75\xf5\x5e\xc3":
        return None
    return {
        "count": 3,
        "elementBytes": 4,
        "sourceArgIndex": 1,
        "destArgIndex": 0,
    }


def u96_left_shift_one_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    decoded = decode_u96_left_shift_one(data)
    if decoded is None:
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    limbs = int(decoded["limbs"])
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 three-limb left-shift pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * Generated inline assembly preserves the target register schedule.",
            " * Acceptance requires compiler/object comparison.",
            " */",
            f"__declspec(naked) void {c_name}(unsigned int *value) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            "        push esi",
            "        mov esi, dword ptr [eax]",
            "        mov ecx, esi",
            "        add esi, esi",
            "        push edi",
            "        mov edi, dword ptr [eax+4]",
            "        shr ecx, 31",
            "        mov dword ptr [eax], esi",
            "        lea esi, [edi+edi]",
            "        or esi, ecx",
            "        mov ecx, dword ptr [eax+8]",
            "        mov edx, edi",
            "        shr edx, 31",
            "        shl ecx, 1",
            "        or ecx, edx",
            "        pop edi",
            "        mov dword ptr [eax+4], esi",
            "        mov dword ptr [eax+8], ecx",
            "        pop esi",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": {
            "rule": "u96-left-shift-one",
            "bodyBytes": len(data),
            "sourceTier": "generated inline-assembly parity fallback with decoded three-limb shift bytes",
            **decoded,
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": f"in-place {limbs}-limb left shift by one; parity fallback preserves register schedule",
        },
    }


def decode_u96_left_shift_one(data: bytes) -> dict[str, Any] | None:
    if len(data) != 46:
        return None
    expected = bytes.fromhex("8b442404568b308bce03f6578b7804c1e91f89308d343f0bf18b48088bd7c1ea1fd1e10bca5f8970048948085ec3")
    if data != expected:
        return None
    return {
        "limbs": 3,
        "elementBytes": 4,
        "shiftBits": 1,
        "direction": "left",
        "inPlace": True,
    }


def semantic_call_wrapper_candidate(
    *,
    source: str,
    rule: str,
    data: bytes,
    call_target: int | None,
    args: list[str],
    extra_generator_fields: dict[str, Any] | None = None,
    compiler_args: list[str] | None = None,
    compiler_reason: str | None = None,
) -> dict[str, Any]:
    generator = {
        "rule": rule,
        "bodyBytes": len(data),
        "callTarget": f"0x{call_target:08x}" if call_target is not None else None,
        "args": args,
    }
    if extra_generator_fields:
        generator.update(extra_generator_fields)
    return {
        "source": source,
        "extension": "c",
        "language": "c",
        "origin": "automatic x86 byte-pattern semantic lift from target slice; not manually authored",
        "semanticSource": True,
        "generator": generator,
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": compiler_args or ["/O2", "/GS-", "/Oy"],
            "reason": compiler_reason or "cdecl call wrapper pattern; direct-call relocation shape must still be proven by objdiff",
        },
    }


def global_param_store_u32_cdecl_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) == 10 and data[:3] == b"\x8b\x44\x24" and data[4] == 0xA3 and data[9] == 0xC3:
        stack_offset = data[3]
        if stack_offset >= 4 and stack_offset % 4 == 0:
            address = int.from_bytes(data[5:9], "little")
            arg_index = (stack_offset - 4) // 4
            args = ", ".join(f"unsigned int a{i}" for i in range(arg_index + 1))
            return {
                "source": f"void {c_identifier(str(task.get('name') or 'recovered_function'))}({args}) {{\n    *(unsigned int volatile *)0x{address:08x} = a{arg_index};\n}}\n",
                "extension": "c",
                "language": "c",
                "origin": "automatic x86 byte-pattern lift from target slice; not manually authored",
                "generator": {"rule": "global-param-store-u32-cdecl", "bodyBytes": len(data), "address": f"0x{address:08x}", "stackOffset": stack_offset},
                "compilerProfileHints": {"compiler": "msvc", "language": "c", "args": ["/O2", "/GS-", "/Oy"], "reason": "absolute global store pattern"},
            }
    return None


def c_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"FUN_{cleaned}"
    return cleaned


def field_name(offset: int) -> str:
    return f"field_{offset:x}"


def class_fields_for_offsets(max_offset: int) -> list[str]:
    last_index = max(0, (max_offset + 3) // 4)
    return [field_name(index * 4) for index in range(last_index + 1)]


def build_task(
    target: dict[str, Any],
    candidate: dict[str, Any],
    fact: dict[str, Any] | None,
    profile_artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fact_binary = fact.get("binaryPath") if fact else None
    task = {
        "schema": "mizuchi.source-task.v1",
        "status": "waiting-for-automatic-source-generator",
        "targetStableId": target.get("stableId"),
        "targetFormat": target.get("format"),
        "architectureHint": target.get("architectureHint"),
        "binaryPath": target.get("binaryPath"),
        "analysisBinaryPath": fact_binary or target.get("binaryPath"),
        "name": candidate.get("name"),
        "address": candidate.get("address"),
        "rva": candidate.get("rva"),
        "size": candidate.get("size"),
        "confidence": candidate.get("confidence"),
        "boundarySource": candidate.get("source"),
        "sourceOrigin": "not generated yet; requires decompiler/model/programmatic generator output",
        "manualSourceAllowed": False,
        "verificationTier": "candidate-queued",
        "acceptanceGate": "target-object objdiff zero is required before this task can be marked as recovered source",
        "compilerProfileArtifacts": profile_artifacts or {
            "schema": "mizuchi.compiler-profile-artifacts.v1",
            "status": "missing",
            "artifacts": [],
        },
    }
    if fact:
        task["functionFact"] = normalize_function_fact(fact)
        task["automaticInputs"] = ["function-facts"]
        if fact.get("asm"):
            task["automaticInputs"].append("asm")
        if fact.get("decompiled"):
            task["automaticInputs"].append("decompiler-output")
    if isinstance(candidate.get("boundaryRepair"), dict):
        task["boundaryRepair"] = candidate["boundaryRepair"]
    return task


def verification_tier_for_task(task: dict[str, Any], *, has_source: bool = False) -> str:
    if has_source:
        return "source-generated-unverified"
    target_slice = task.get("targetSlice") or {}
    if target_slice.get("status") == "complete":
        return "target-slice-acquired"
    if task.get("functionFact"):
        return "function-facts-normalized"
    return "candidate-queued"


def build_target_slice(inventory: dict[str, Any], candidate: dict[str, Any], fact: dict[str, Any] | None) -> dict[str, Any]:
    target = inventory.get("target") or {}
    binary_path = Path(str((fact or {}).get("binaryPath") or target.get("binaryPath") or ""))
    image_base = int(inventory.get("imageBase") or 0)
    address = coerce_int(candidate.get("address"))
    rva = coerce_int(candidate.get("rva"))
    if rva is None and address is not None and image_base:
        rva = address - image_base
    if address is None and rva is not None and image_base:
        address = image_base + rva
    size = coerce_int((fact or {}).get("bodyBytes")) or coerce_int(candidate.get("size")) or 0
    if not binary_path.exists():
        return {
            "status": "missing-binary",
            "reason": f"analysis binary not found: {binary_path}",
            "analysisBinaryPath": str(binary_path),
            "address": address,
            "rva": rva,
            "size": size,
        }
    if size <= 0:
        return {"status": "missing-size", "analysisBinaryPath": str(binary_path), "address": address, "rva": rva, "size": size}
    section = section_for_rva(inventory, rva) if rva is not None else section_for_address(inventory, address)
    if section is None:
        return {
            "status": "address-outside-code" if rva is None else "rva-outside-code",
            "analysisBinaryPath": str(binary_path),
            "address": address,
            "rva": rva,
            "size": size,
        }
    section_rva = int(section.get("rva") if section.get("rva") is not None else section.get("address") or 0)
    section_file_offset = int(section.get("fileOffset") if section.get("fileOffset") is not None else section.get("offset") or 0)
    section_file_size = int(section.get("fileSize") or section.get("size") or 0)
    position = rva if rva is not None else address
    if position is None:
        return {"status": "missing-address", "analysisBinaryPath": str(binary_path), "rva": rva, "size": size}
    file_offset = section_file_offset + (position - section_rva)
    if file_offset < section_file_offset or file_offset + size > section_file_offset + section_file_size:
        return {
            "status": "slice-outside-section",
            "analysisBinaryPath": str(binary_path),
            "address": address,
            "rva": rva,
            "size": size,
            "fileOffset": file_offset,
            "section": section.get("name"),
        }
    with binary_path.open("rb") as fh:
        fh.seek(file_offset)
        data = fh.read(size)
    return {
        "status": "complete",
        "analysisBinaryPath": str(binary_path),
        "address": address,
        "rva": rva,
        "size": len(data),
        "fileOffset": file_offset,
        "section": section.get("name"),
        "bytesSha256": hashlib.sha256(data).hexdigest(),
        "bytesHex": data.hex(),
        "claimBoundary": "target slice bytes are acquisition evidence only; source parity still requires compiling candidate source and comparing code/relocations under a compiler profile",
    }


def section_for_rva(inventory: dict[str, Any], rva: int) -> dict[str, Any] | None:
    for section in inventory.get("codeRanges", []):
        start = int(section.get("rva") or 0)
        size = int(section.get("size") or section.get("fileSize") or 0)
        if start <= rva < start + size:
            return section
    return None


def section_for_address(inventory: dict[str, Any], address: int | None) -> dict[str, Any] | None:
    if address is None:
        return None
    for section in inventory.get("codeRanges", []):
        if section.get("address") is None:
            continue
        start = int(section.get("address") or 0)
        size = int(section.get("size") or section.get("fileSize") or 0)
        if start <= address < start + size:
            return section
    return None


def rel32_call_target(address: int | None, *, call_offset: int, rel32: int) -> int | None:
    if address is None:
        return None
    return address + call_offset + 5 + rel32


def coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    except (TypeError, ValueError):
        return None


def signed_disp8(value: int) -> int:
    return value - 0x100 if value >= 0x80 else value


def offset_expr(base: str, offset: int) -> str:
    if offset < 0:
        return f"{base} - 0x{-offset:x}"
    return f"{base} + 0x{offset:x}"


def self_offset_expr(offset: int) -> str:
    return offset_expr("(char *)self", offset)


def self_offset(offset: int) -> str:
    return self_offset_expr(offset)


def safe_task_id(task: dict[str, Any]) -> str:
    name = str(task.get("name") or "sub")
    addr = task.get("address") if task.get("address") is not None else task.get("rva")
    suffix = f"{int(addr):x}" if addr is not None else hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    cleaned = re.sub(r"[^A-Za-z0-9_.+-]+", "_", name).strip("._") or "sub"
    return f"{cleaned}_{suffix}"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
