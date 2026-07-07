#!/usr/bin/env python3
"""Build matched-example and triage indexes for source-parity automation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_bytes(row: dict[str, Any]) -> bytes:
    try:
        return bytes.fromhex(str(row.get("bytes", "")))
    except ValueError:
        return b""


def ngrams(data: bytes, n: int) -> set[str]:
    if len(data) < n:
        return set()
    return {data[i : i + n].hex() for i in range(len(data) - n + 1)}


def byte_hist(data: bytes) -> dict[int, int]:
    return dict(Counter(data))


def cosine(left: dict[int, int], right: dict[int, int]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    dot = sum(left.get(key, 0) * right.get(key, 0) for key in keys)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def size_similarity(left: int, right: int) -> float:
    if max(left, right) == 0:
        return 0.0
    return 1.0 - (abs(left - right) / max(left, right))


def classify(data: bytes, row: dict[str, Any]) -> list[str]:
    tags: set[str] = set()
    size = int(row.get("bodyBytes") or len(data))
    instrs = int(row.get("instructionCount") or 0)
    if size <= 12:
        tags.add("tiny")
    elif size <= 32:
        tags.add("small")
    elif size >= 256:
        tags.add("large")
    if data.startswith(b"\x55\x8b\xec"):
        tags.add("ebp-frame")
    if data.endswith(b"\xc3") or b"\xc3" in data[-2:]:
        tags.add("cdecl-ret")
    if data.endswith(b"\xc2") or b"\xc2" in data[-4:]:
        tags.add("stdcall-ret")
    if b"\xe8" in data:
        tags.add("direct-call")
    if b"\xe9" in data or data.startswith(b"\xeb"):
        tags.add("direct-jump")
    if b"\xff\x25" in data or data.startswith(b"\xff\x25"):
        tags.add("import-thunk")
    if b"\xff\x50" in data or b"\xff\x51" in data or b"\xff\x60" in data or b"\xff\x61" in data:
        tags.add("virtual-or-indirect-tail")
    if data.startswith((b"\x8b\x41", b"\x8a\x41", b"\x0f\xb6\x41", b"\x8d\x41")):
        tags.add("fastcall-accessor")
    if b"\xa1" in data or b"\xa3" in data or b"\x05" in data[:2]:
        tags.add("absolute-global")
    if b"\xc7\x01" in data:
        tags.add("vtable-store")
    if b"\x33\xc0" in data or b"\x31\xc0" in data:
        tags.add("zero-return")
    if b"\x40" in data or b"\x48" in data:
        tags.add("inc-dec-era-idiom")
    if b"\x6a" in data and b"\x58" in data:
        tags.add("push-pop-era-idiom")
    if instrs <= 4:
        tags.add("compiler-profile-probe")
    if row.get("section") != ".textV":
        tags.add("non-textv")
    return sorted(tags)


def source_info(row: dict[str, Any]) -> dict[str, Any]:
    out_dir = Path(str(row.get("outDir", "")))
    candidate = out_dir / "candidate.c"
    if not candidate.exists():
        return {}
    text = candidate.read_text(encoding="utf-8")
    return {
        "candidateSource": str(candidate),
        "sourceSha256": sha256_text(text),
        "sourcePreview": "\n".join(text.strip().splitlines()[:8]),
    }


def feature_row(row: dict[str, Any], *, matched: dict[str, Any] | None = None) -> dict[str, Any]:
    data = parse_bytes(row)
    tags = classify(data, row)
    output = {
        "schema": "reconkit.source-parity-function-features.v1",
        "name": row.get("name"),
        "entry": row.get("entry"),
        "section": row.get("section"),
        "bodyBytes": int(row.get("bodyBytes") or len(data)),
        "instructionCount": int(row.get("instructionCount") or 0),
        "byteSha256": hashlib.sha256(data).hexdigest(),
        "prefix": data[:8].hex(),
        "suffix": data[-8:].hex(),
        "tags": tags,
        "ngrams2": sorted(ngrams(data, 2)),
        "ngrams3": sorted(ngrams(data, 3)),
        "byteHistogram": byte_hist(data),
    }
    if matched:
        output.update(
            {
                "matched": True,
                "kind": matched.get("kind"),
                "symbol": matched.get("symbol"),
                "outDir": matched.get("outDir"),
                "verifyStatus": matched.get("status"),
                "differences": matched.get("differences"),
            }
        )
        output.update(source_info(matched))
    else:
        output["matched"] = False
        output["strategyClass"] = strategy_class(tags, data)
        output["nextAction"] = next_action(output["strategyClass"])
    return output


def strategy_class(tags: list[str], data: bytes) -> str:
    tagset = set(tags)
    if "import-thunk" in tagset:
        return "import-thunk-model"
    if "virtual-or-indirect-tail" in tagset:
        return "virtual-call-or-thiscall-model"
    if "compiler-profile-probe" in tagset and "direct-call" not in tagset:
        return "compiler-profile-probe"
    if "fastcall-accessor" in tagset or "absolute-global" in tagset:
        return "accessor-source-shape"
    if "ebp-frame" in tagset:
        return "stack-frame-source-shape"
    if "direct-call" in tagset or "direct-jump" in tagset:
        return "relocation-and-prototype-model"
    if len(data) <= 32:
        return "small-pattern-synthesis"
    return "semantic-decompilation-required"


def next_action(strategy: str) -> str:
    return {
        "import-thunk-model": "model imported target symbol and calling convention, then compare relocation-aware object",
        "virtual-call-or-thiscall-model": "recover thiscall/vtable layout and synthesize virtual tailcall candidate",
        "compiler-profile-probe": "add to compiler-profile corpus before AI; use it to score toolchain/flag/source-shape hypotheses",
        "accessor-source-shape": "generate accessor/setter source variants and accept only objdiff zero",
        "stack-frame-source-shape": "run source-shape matrix for locals, temporaries, frame pointer, and zero initialization",
        "relocation-and-prototype-model": "recover callee prototype and relocation target, then synthesize wrapper/body candidate",
        "small-pattern-synthesis": "derive bounded high-level C templates from byte pattern and matched examples",
        "semantic-decompilation-required": "build Ghidra/type context and matched-example prompt before candidate generation",
    }[strategy]


def similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    n2 = jaccard(set(left["ngrams2"]), set(right["ngrams2"]))
    n3 = jaccard(set(left["ngrams3"]), set(right["ngrams3"]))
    hist = cosine({int(k): v for k, v in left["byteHistogram"].items()}, {int(k): v for k, v in right["byteHistogram"].items()})
    size = size_similarity(int(left["bodyBytes"]), int(right["bodyBytes"]))
    tags = jaccard(set(left["tags"]), set(right["tags"]))
    return round((0.35 * n3) + (0.2 * n2) + (0.2 * hist) + (0.15 * size) + (0.1 * tags), 6)


def retrieve(remaining: list[dict[str, Any]], matched: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    rows = []
    for item in remaining:
        scored = []
        for example in matched:
            score = similarity(item, example)
            if score <= 0:
                continue
            scored.append(
                {
                    "name": example["name"],
                    "entry": example["entry"],
                    "kind": example.get("kind"),
                    "score": score,
                    "tags": example.get("tags", []),
                    "candidateSource": example.get("candidateSource"),
                }
            )
        scored.sort(key=lambda row: row["score"], reverse=True)
        rows.append(
            {
                "schema": "reconkit.source-parity-retrieval.v1",
                "name": item["name"],
                "entry": item["entry"],
                "strategyClass": item["strategyClass"],
                "tags": item["tags"],
                "nearestMatchedExamples": scored[:top_k],
                "nextAction": item["nextAction"],
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--inventory", type=Path, default=ROOT / "target/swkotor-unpack/facts/function-inventory.jsonl")
    parser.add_argument("--queue", type=Path, default=ROOT / "target/swkotor-recovery-queue/queue.jsonl")
    parser.add_argument(
        "--matched-summary",
        type=Path,
        action="append",
        default=[
            ROOT / "target/swkotor-trivial-matches/summary.jsonl",
            ROOT / "target/swkotor-reloc-wrapper-matches/summary.jsonl",
        ],
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "target/source-parity-index/swkotor")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-remaining", type=int, default=500)
    args = parser.parse_args()

    inventory = list(iter_jsonl(args.inventory))
    by_key = {(str(row.get("name")), str(row.get("entry"))): row for row in inventory}
    matched_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for path in args.matched_summary:
        for row in iter_jsonl(path):
            if row.get("status") == "matched" and row.get("differences") == 0:
                matched_rows[(str(row.get("name")), str(row.get("entry")))] = row

    matched_features = []
    for key, match in sorted(matched_rows.items(), key=lambda item: item[0][1]):
        inv = by_key.get(key)
        if inv is None:
            continue
        matched_features.append(feature_row(inv, matched=match))

    queue_entries = list(iter_jsonl(args.queue))[: args.max_remaining]
    remaining_features = []
    for queued in queue_entries:
        key = (str(queued.get("name")), str(queued.get("entry")))
        inv = by_key.get(key, queued)
        remaining_features.append(feature_row(inv))

    retrieval_rows = retrieve(remaining_features, matched_features, args.top_k)

    strategy_counts = Counter(row["strategyClass"] for row in remaining_features)
    tag_counts = Counter(tag for row in remaining_features for tag in row["tags"])
    strategy = {
        "schema": "reconkit.source-parity-strategy-index.v1",
        "inventory": str(args.inventory),
        "queue": str(args.queue),
        "matchedSummaries": [str(path) for path in args.matched_summary],
        "matchedExamples": len(matched_features),
        "remainingIndexed": len(remaining_features),
        "topK": args.top_k,
        "strategyClassCounts": dict(sorted(strategy_counts.items())),
        "tagCounts": dict(sorted(tag_counts.items())),
        "compilerProfileCorpusSeeds": [
            row for row in retrieval_rows if row["strategyClass"] == "compiler-profile-probe"
        ][:25],
        "hardestNearTermClasses": [
            "stack-frame-source-shape",
            "virtual-call-or-thiscall-model",
            "relocation-and-prototype-model",
            "semantic-decompilation-required",
        ],
        "claimBoundary": "similarity and retrieval guide candidate generation only; objdiff zero remains the acceptance gate",
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    matched_path = args.out_dir / "matched-examples.jsonl"
    remaining_path = args.out_dir / "remaining-features.jsonl"
    retrieval_path = args.out_dir / "retrieval.jsonl"
    strategy_path = args.out_dir / "strategy.json"
    summary_path = args.out_dir / "summary.json"
    write_jsonl(matched_path, matched_features)
    write_jsonl(remaining_path, remaining_features)
    write_jsonl(retrieval_path, retrieval_rows)
    write_json(strategy_path, strategy)
    summary = {
        "schema": "reconkit.source-parity-feature-index-summary.v1",
        "matchedExamples": len(matched_features),
        "remainingIndexed": len(remaining_features),
        "matchedExamplesPath": str(matched_path),
        "remainingFeaturesPath": str(remaining_path),
        "retrievalPath": str(retrieval_path),
        "strategyPath": str(strategy_path),
        "strategyClassCounts": strategy["strategyClassCounts"],
    }
    write_json(summary_path, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
