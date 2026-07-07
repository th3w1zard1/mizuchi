#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHONPATH="$ROOT/src" python3 - <<'PY'
from reconkit_re.functions import dedupe_same_address_candidates
from reconkit_re.sourcegen import address_alias_key, build_address_alias_metadata

rows = [
    {
        "name": "_BinkGetError@0",
        "address": 0x30001000,
        "rva": 0x1000,
        "size": 0,
        "source": "pe-export",
        "confidence": "high",
        "evidence": {"sources": ["pe-export"]},
    },
    {
        "name": "sub_1000",
        "address": 0x30001000,
        "rva": 0x1000,
        "size": 7,
        "source": "x86-prologue",
        "confidence": "medium",
        "evidence": {"sources": ["x86-prologue"]},
    },
    {
        "name": "_BinkGetError@0",
        "address": 0x30001000,
        "rva": 0x1000,
        "size": 0,
        "source": "objdump-label",
        "confidence": "medium",
        "evidence": {"sources": ["objdump-label"], "section": ".text"},
    },
]

merged = dedupe_same_address_candidates(rows)
assert len(merged) == 1, merged
row = merged[0]
assert row["name"] == "_BinkGetError@0", row
assert row["source"] == "pe-export", row
assert row["size"] == 7, row
assert row["confidence"] == "high", row
assert row["aliasCount"] == 3, row
assert row["evidence"]["sources"] == ["objdump-label", "pe-export", "x86-prologue"], row
assert row["evidence"]["duplicateCandidateCount"] == 3, row
assert [alias["source"] for alias in row["evidence"]["aliases"]] == ["pe-export", "objdump-label", "x86-prologue"], row

metadata, groups = build_address_alias_metadata(merged)
assert len(groups) == 1, groups
group = groups[0]
assert group["canonicalAddress"] == "0x30001000", group
assert group["canonicalName"] == "_BinkGetError@0", group
assert group["aliasCount"] == 3, group
assert group["scheduledCandidateCount"] == 1, group
assert group["duplicateAddressAliases"] == 2, group
assert group["duplicateAddressScheduledTasks"] == 0, group
assert sorted(alias["source"] for alias in group["aliases"]) == ["objdump-label", "pe-export", "x86-prologue"], group

alias = metadata[address_alias_key(row)]
assert alias["role"] == "primary", alias
assert alias["canonicalName"] == "_BinkGetError@0", alias
assert alias["aliasCount"] == 3, alias
PY

echo "ok"
