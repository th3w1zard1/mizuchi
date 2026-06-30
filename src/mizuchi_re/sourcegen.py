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
    original_candidates = list(function_candidates.get("candidates", []))
    all_candidates = [row for row in original_candidates if is_recoverable_candidate(row)]
    inferred_sizes = infer_candidate_sizes(all_candidates, inventory or {})
    start = max(0, offset)
    candidates = all_candidates[start : start + max(limit, 0)]

    tasks_path = out_dir / "tasks.jsonl"
    generated_count = 0
    fresh_generated_count = 0
    reused_generated_count = 0
    target_slice_count = 0
    task_count = 0
    by_status: dict[str, int] = {}
    target_slices_by_status: dict[str, int] = {}
    inferred_size_count = 0

    with tasks_path.open("w", encoding="utf-8") as tasks:
        for row in candidates:
            if is_range_alias(row, facts):
                continue
            row = with_inferred_size(row, inferred_sizes)
            if row.get("sizeSource") == "inferred-next-candidate-boundary":
                inferred_size_count += 1
            fact = match_fact(row, facts)
            task = build_task(target, row, fact)
            target_slice = build_target_slice(inventory or {}, row, fact)
            target_slice_bytes: bytes | None = None
            if target_slice.get("status") == "complete":
                slices_dir.mkdir(parents=True, exist_ok=True)
                slice_path = slices_dir / f"{safe_task_id(task)}.target.bin"
                slice_bytes = bytes.fromhex(str(target_slice.pop("bytesHex")))
                target_slice_bytes = slice_bytes
                slice_path.write_bytes(slice_bytes)
                target_slice["bytesPath"] = str(slice_path)
                target_slice_count += 1
            target_slice_status = str(target_slice.get("status") or "unknown")
            target_slices_by_status[target_slice_status] = target_slices_by_status.get(target_slice_status, 0) + 1
            task["targetSlice"] = target_slice
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
                        "acceptanceGate": "compile with selected compiler profile and objdiff-zero against target slice",
                    }
                )
                write_json(case_dir / "candidate.json", task)
                generated_count += 1
                fresh_generated_count += 1
            elif generated_candidate is not None:
                case_dir.mkdir(parents=True, exist_ok=True)
                source = str(generated_candidate["source"]).rstrip() + "\n"
                source_path = case_dir / f"candidate.{generated_candidate['extension']}"
                source_path.write_text(source, encoding="utf-8")
                task.update(
                    {
                        "status": "generated-unverified",
                        "source": str(source_path),
                        "sourceSha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "sourceLanguage": generated_candidate["language"],
                        "sourceOrigin": generated_candidate["origin"],
                        "semanticSource": True,
                        "automaticGenerator": generated_candidate["generator"],
                        "compilerProfileHints": generated_candidate["compilerProfileHints"],
                        "acceptanceGate": "compile with selected compiler profile and objdiff-zero against target slice",
                    }
                )
                task.setdefault("automaticInputs", [])
                for item in ["target-slice-bytes", str(generated_candidate["generator"].get("rule") or "byte-pattern-generator")]:
                    if item not in task["automaticInputs"]:
                        task["automaticInputs"].append(item)
                write_json(case_dir / "candidate.json", task)
                generated_count += 1
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
                        "acceptanceGate": "compile with selected compiler profile and objdiff-zero against target slice",
                    }
                )
                task.setdefault("automaticInputs", ["function-facts"])
                if "existing-candidate-file" not in task["automaticInputs"]:
                    task["automaticInputs"].append("existing-candidate-file")
                write_json(case_dir / "candidate.json", task)
                generated_count += 1
                reused_generated_count += 1
            tasks.write(json.dumps(task, sort_keys=True) + "\n")
            task_count += 1
            by_status[str(task["status"])] = by_status.get(str(task["status"]), 0) + 1

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
        "freshGeneratedSourceCandidates": fresh_generated_count,
        "reusedSourceCandidates": reused_generated_count,
        "targetSlices": target_slice_count,
        "targetSlicesByStatus": dict(sorted(target_slices_by_status.items())),
        "inferredFunctionSizes": inferred_size_count,
        "candidateOffset": start,
        "candidateLimit": max(limit, 0),
        "candidateTotal": len(all_candidates),
        "originalCandidateTotal": len(original_candidates),
        "functionFacts": str(function_facts_jsonl) if function_facts_jsonl else None,
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


def infer_candidate_sizes(candidates: list[dict[str, Any]], inventory: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
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
    return inferred


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
    return {
        **candidate,
        "size": int(inferred["size"]),
        "sizeSource": inferred["sizeSource"],
        "sizeConfidence": inferred["sizeConfidence"],
        "evidence": evidence,
    }


def generated_candidate_from_target_bytes(task: dict[str, Any], data: bytes | None) -> dict[str, Any] | None:
    if not data:
        return None
    body = strip_alignment_padding(data)
    generators = [
        inc_abs_global_candidate,
        virtual_tailcall_candidate,
        unsigned_field_less_than_candidate,
        zero_return_candidate,
        zero_return_stdcall_candidate,
        one_return_candidate,
        one_return_stdcall_candidate,
        return_first_stack_arg_candidate,
        return_first_stack_arg_stdcall_candidate,
        add_two_stack_args_candidate,
        add_two_stack_args_stdcall_candidate,
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
        global_setter_two_u32_cdecl_candidate,
        global_param_store_u32_cdecl_candidate,
        thiscall_indexed_field_selector,
    ]
    for generator in generators:
        candidate = generator(task, body)
        if candidate is not None:
            return candidate
    return None


def strip_alignment_padding(data: bytes) -> bytes:
    end = len(data)
    while end > 0 and data[end - 1] in {0x90, 0xCC}:
        end -= 1
    return data[:end]


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
    if len(data) != 5 or data[:3] not in {b"\x33\xc0\xc2", b"\x31\xc0\xc2"} or data[3:] != b"\x04\x00":
        return None
    c_name = c_identifier(str(task.get("name") or "recovered_function"))
    source = "\n".join(
        [
            "/*",
            " * Automatically generated from an x86 stdcall zero-return pattern.",
            f" * Target: {task.get('name')} at {task.get('address')}.",
            " * This is an unverified semantic candidate; acceptance requires compiler/object comparison.",
            " */",
            f"int __stdcall {c_name}(unsigned int unused) {{",
            "    (void)unused;",
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
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall constant zero return is a canonical x86 leaf pattern",
        },
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
    if len(data) != 9 or data != b"\x8b\x44\x24\x04\x03\x44\x24\x08\xc3":
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
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "simple two-arg arithmetic is a canonical x86 leaf pattern",
        },
    }


def add_two_stack_args_stdcall_candidate(task: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    if len(data) != 11 or data != b"\x8b\x44\x24\x04\x03\x44\x24\x08\xc2\x08\x00":
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
        },
        "compilerProfileHints": {
            "compiler": "msvc",
            "language": "c",
            "args": ["/O2", "/GS-", "/Oy"],
            "reason": "stdcall two-arg arithmetic is a canonical x86 leaf pattern",
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


def build_task(target: dict[str, Any], candidate: dict[str, Any], fact: dict[str, Any] | None) -> dict[str, Any]:
    fact_binary = fact.get("binaryPath") if fact else None
    task = {
        "schema": "mizuchi.source-task.v1",
        "status": "waiting-for-automatic-source-generator",
        "targetStableId": target.get("stableId"),
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
    }
    if fact:
        task["functionFact"] = {
            "name": fact.get("name"),
            "entry": fact.get("entry"),
            "entryOffset": fact.get("entryOffset"),
            "bodyBytes": fact.get("bodyBytes"),
            "instructionCount": fact.get("instructionCount"),
            "bytesSha256": hashlib.sha256(str(fact.get("bytes") or "").encode("utf-8")).hexdigest(),
            "hasAsm": bool(fact.get("asm")),
            "hasDecompilerOutput": bool(fact.get("decompiled")),
        }
        task["automaticInputs"] = ["function-facts"]
        if fact.get("asm"):
            task["automaticInputs"].append("asm")
        if fact.get("decompiled"):
            task["automaticInputs"].append("decompiler-output")
    return task


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
    if rva is None:
        return {"status": "missing-rva", "analysisBinaryPath": str(binary_path), "address": address, "size": size}
    if size <= 0:
        return {"status": "missing-size", "analysisBinaryPath": str(binary_path), "address": address, "rva": rva, "size": size}
    section = section_for_rva(inventory, rva)
    if section is None:
        return {
            "status": "rva-outside-code",
            "analysisBinaryPath": str(binary_path),
            "address": address,
            "rva": rva,
            "size": size,
        }
    section_rva = int(section.get("rva") or 0)
    section_file_offset = int(section.get("fileOffset") or 0)
    section_file_size = int(section.get("fileSize") or section.get("size") or 0)
    file_offset = section_file_offset + (rva - section_rva)
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


def coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
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
