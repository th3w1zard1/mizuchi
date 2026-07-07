#!/usr/bin/env python3
"""
normalize-binary.py — anomaly-detect → unpack/normalize front-end for reconkit.

The matching-decompilation pipeline can only work on *readable* machine code.
Real-world executables are frequently packed/encrypted/protected, so their
on-disk code is not directly disassemblable. This stage runs BEFORE the
decompilation logic: it detects such anomalies and, when recoverable, produces
a normalized (unpacked) binary whose code sections are plaintext.

Design contract (honest by construction):
  - A protector that only *encrypts/compresses* code (SecuROM, SafeDisc, UPX,
    ASPack, …) is RECOVERABLE: the original instructions exist, just hidden, so
    we can unpack and hand real code to the decompiler.
  - A protector that *virtualizes* code (VMProtect, Themida/WinLicense mutation)
    replaces instructions with bytecode for a bespoke VM. The original
    instructions are GONE. We detect and report this rather than emit fake
    source. `recoverable=false` is a truthful verdict, not a failure to hide.

Output: a JSON report on stdout and, when an unpack succeeds, a normalized
binary at --out.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field


# ---------------------------------------------------------------------------
# Minimal PE parsing (stdlib only; pefile not required)
# ---------------------------------------------------------------------------
@dataclass
class Section:
    name: str
    vaddr: int
    vsize: int
    raddr: int
    rsize: int
    characteristics: int
    entropy: float


@dataclass
class PEInfo:
    machine: int
    is_pe32_plus: bool
    entry_rva: int
    image_base: int
    sections: list[Section]


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def parse_pe(data: bytes) -> PEInfo | None:
    if data[:2] != b"MZ":
        return None
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        return None
    machine, nsec = struct.unpack_from("<HH", data, pe + 4)
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    is_plus = magic == 0x20B
    entry_rva = struct.unpack_from("<I", data, opt + 16)[0]
    image_base = (
        struct.unpack_from("<Q", data, opt + 24)[0]
        if is_plus
        else struct.unpack_from("<I", data, opt + 28)[0]
    )
    sh = opt + (240 if is_plus else 224)
    sections: list[Section] = []
    for s in range(nsec):
        off = sh + s * 40
        name = data[off : off + 8].rstrip(b"\0").decode("latin1", "replace")
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, off + 8)
        chars = struct.unpack_from("<I", data, off + 36)[0]
        body = data[raddr : raddr + rsize]
        sections.append(
            Section(name, vaddr, vsize, raddr, rsize, chars, shannon_entropy(body))
        )
    return PEInfo(machine, is_plus, entry_rva, image_base, sections)


def section_at_rva(pe: PEInfo, rva: int) -> Section | None:
    for s in pe.sections:
        if s.vaddr <= rva < s.vaddr + max(s.vsize, s.rsize):
            return s
    return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
# name -> (recoverable, list of ascii signatures to search in the whole file)
PROTECTOR_SIGNATURES: dict[str, tuple[bool, list[bytes]]] = {
    "UPX": (True, [b"UPX!", b"UPX0", b"UPX1"]),
    "SecuROM": (True, [b"PauseAndPlay", b".securom", b"AddD", b"CMS_t"]),
    "SafeDisc": (True, [b"BoG_", b"stxt774", b"stxt371", b".txt2"]),
    "ASPack": (True, [b".aspack", b".adata", b"ASPack"]),
    "ASProtect": (True, [b".asprotect", b"ASProtect"]),
    "PECompact": (True, [b"PEC2", b"PECompact2"]),
    "FSG": (True, [b"FSG!"]),
    "MPRESS": (True, [b".MPRESS1", b".MPRESS2"]),
    "Petite": (True, [b".petite"]),
    "tElock": (True, [b"tElock"]),
    # Virtualizers — NOT recoverable to original source (code is gone).
    "VMProtect": (False, [b".vmp0", b".vmp1", b".vmp2", b"VMProtect"]),
    "Themida/WinLicense": (False, [b".themida", b"Themida", b".winlice", b"WinLicense"]),
    "Enigma": (False, [b".enigma1", b".enigma2", b"Enigma"]),
}

# Section names that, when present, strongly imply a protector even w/o strings.
SUSPECT_SECTION_NAMES = {
    ".bind": "SecuROM",
    ".vmp0": "VMProtect",
    ".vmp1": "VMProtect",
    ".themida": "Themida/WinLicense",
    ".aspack": "ASPack",
    ".adata": "ASPack",
}

HIGH_ENTROPY = 7.2  # bits/byte; code sections normally ~6.0-6.6


@dataclass
class Detection:
    packed: bool
    protector: str
    recoverable: bool
    confidence: str
    evidence: list[str] = field(default_factory=list)
    encrypted_code_sections: list[str] = field(default_factory=list)


def detect(data: bytes, pe: PEInfo) -> Detection:
    evidence: list[str] = []
    found: dict[str, bool] = {}  # protector -> recoverable

    # 1. signature scan
    for prot, (recoverable, sigs) in PROTECTOR_SIGNATURES.items():
        for sig in sigs:
            if sig in data:
                found[prot] = recoverable
                evidence.append(f"signature {sig!r} -> {prot}")
                break

    # 2. suspect section names
    for s in pe.sections:
        if s.name in SUSPECT_SECTION_NAMES:
            prot = SUSPECT_SECTION_NAMES[s.name]
            found.setdefault(prot, PROTECTOR_SIGNATURES.get(prot, (True, []))[0])
            evidence.append(f"section {s.name!r} -> {prot}")

    # 3. entropy of executable sections
    IMAGE_SCN_MEM_EXECUTE = 0x20000000
    IMAGE_SCN_CNT_CODE = 0x00000020
    enc_code: list[str] = []
    for s in pe.sections:
        is_exec = bool(s.characteristics & (IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_CNT_CODE))
        if is_exec and s.entropy >= HIGH_ENTROPY and s.rsize > 0x400:
            enc_code.append(s.name)
            evidence.append(f"section {s.name!r} entropy {s.entropy:.3f} >= {HIGH_ENTROPY} (executable)")

    # 4. entry point in a non-standard / high-entropy section
    ep_sec = section_at_rva(pe, pe.entry_rva)
    if ep_sec and ep_sec.name not in (".text", "CODE"):
        evidence.append(f"entry point in non-standard section {ep_sec.name!r}")

    # Readability is decided by ENTROPY, not by leftover strings. A no-CD /
    # cracked / already-unpacked build keeps the protector's signature strings
    # (.bind, "PauseAndPlay") but its code section is plaintext (normal entropy
    # ~6.5). Such a binary is directly decompilable — do not treat it as packed.
    ep_in_code = ep_sec is not None and ep_sec.name in (".text", "CODE") and ep_sec.entropy < HIGH_ENTROPY
    if not enc_code and ep_in_code:
        note = "code section is plaintext (normal entropy); directly decompilable"
        if found:
            note += f" — leftover {next(iter(found))} signature strings present but code is NOT encrypted"
        return Detection(False, "none(readable)", True, "high", evidence + [note], [])

    if not found and not enc_code:
        return Detection(False, "none", True, "high", evidence or ["no protector signatures; code-section entropy normal"], [])

    # Pick the protector: prefer a named hit; non-recoverable (VM) wins if present.
    protector = "unknown-packer"
    recoverable = True
    if found:
        # If any virtualizer present, that dominates the verdict.
        vms = [p for p, rec in found.items() if not rec]
        if vms:
            protector = vms[0]
            recoverable = False
        else:
            protector = next(iter(found))
            recoverable = True
    elif enc_code:
        protector = "unknown-encryptor"
        recoverable = True  # encrypted (not virtualized) -> dump is possible

    confidence = "high" if found else ("medium" if enc_code else "low")
    return Detection(True, protector, recoverable, confidence, evidence, enc_code)


# ---------------------------------------------------------------------------
# Unpackers
# ---------------------------------------------------------------------------
def unpack_upx(src: str, out: str) -> tuple[bool, str]:
    if not shutil.which("upx"):
        return False, "upx not installed"
    shutil.copyfile(src, out)
    r = subprocess.run(["upx", "-d", out], capture_output=True, text=True)
    if r.returncode == 0:
        return True, "upx -d succeeded"
    return False, f"upx -d failed: {r.stderr.strip() or r.stdout.strip()}"


def unpack_dynamic_wine(src: str, out: str, pe: PEInfo, det: Detection, timeout: int) -> tuple[bool, str]:
    """
    Generic dynamic unpack for encrypt-only protectors (SecuROM/SafeDisc/...).
    Strategy: launch the PE under wine so its loader stub decrypts code in
    memory, then read the decrypted executable pages from /proc/<pid>/mem and
    splice them back into a copy of the file's raw section data (OEP-dump).

    This is best-effort: SecuROM/SafeDisc carry anti-debug/anti-VM and GUI
    games may never reach a stable, dumpable steady state under headless wine.
    On failure we report exactly why — we never emit a partial dump as success.
    """
    if not shutil.which("wine"):
        return False, "wine not installed"

    enc_secs = [s for s in pe.sections if s.name in det.encrypted_code_sections]
    if not enc_secs:
        return False, "no encrypted executable sections identified to dump"

    env = dict(os.environ)
    env.setdefault("WINEDEBUG", "-all")
    # Headless: virtual desktop / no display. Many protected games still need a
    # GPU; this will surface as a launch failure we report honestly.
    proc = subprocess.Popen(
        ["wine", src],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=os.path.dirname(os.path.abspath(src)) or ".",
    )

    target = enc_secs[0]
    deadline = time.time() + timeout
    dumped: bytes | None = None
    last_entropy = None
    try:
        while time.time() < deadline:
            time.sleep(2)
            # Find the wine child actually mapping our image.
            pid = _find_image_pid(os.path.basename(src))
            if pid is None:
                if proc.poll() is not None:
                    return False, "wine process exited before code was decrypted (protector refused to run under wine / missing GPU or DRM check)"
                continue
            region = _read_decrypted_region(pid, pe.image_base + target.vaddr, target.vsize)
            if region is None:
                continue
            last_entropy = shannon_entropy(region[: min(len(region), 0x10000)])
            if last_entropy < HIGH_ENTROPY:  # decrypted!
                dumped = region
                break
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        subprocess.run(["wineserver", "-k"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if dumped is None:
        ent = f" (last observed entropy {last_entropy:.3f})" if last_entropy is not None else ""
        return False, f"could not capture decrypted {target.name} within {timeout}s{ent}"

    # Splice decrypted bytes back into a copy of the file (raw OEP dump).
    data = bytearray(open(src, "rb").read())
    n = min(len(dumped), target.rsize)
    data[target.raddr : target.raddr + n] = dumped[:n]
    with open(out, "wb") as f:
        f.write(data)
    return True, f"dumped decrypted {target.name} from wine pid (entropy {last_entropy:.3f})"


def _find_image_pid(image_name: str) -> int | None:
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/maps") as m:
                    if image_name.lower() in m.read().lower():
                        return int(pid)
            except OSError:
                continue
    except OSError:
        pass
    return None


def _read_decrypted_region(pid: int, vaddr: int, size: int) -> bytes | None:
    try:
        with open(f"/proc/{pid}/mem", "rb", 0) as mem:
            mem.seek(vaddr)
            return mem.read(size)
    except (OSError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Detect & normalize (unpack) a binary before decompilation.")
    ap.add_argument("binary")
    ap.add_argument("--out", help="path for the normalized (unpacked) binary")
    ap.add_argument("--timeout", type=int, default=120, help="dynamic-unpack timeout seconds")
    ap.add_argument("--detect-only", action="store_true")
    args = ap.parse_args()

    data = open(args.binary, "rb").read()
    pe = parse_pe(data)
    report: dict = {"binary": args.binary, "size": len(data)}

    if pe is None:
        report.update(format="not-PE", note="ELF/Mach-O/other; PE normalizer not applicable")
        print(json.dumps(report, indent=2))
        return 0

    det = detect(data, pe)
    report["sections"] = [
        {"name": s.name, "entropy": round(s.entropy, 3), "rsize": s.rsize} for s in pe.sections
    ]
    report["detection"] = asdict(det)

    if args.detect_only or not det.packed:
        report["action"] = "none (clean or detect-only)"
        print(json.dumps(report, indent=2))
        return 0

    if not det.recoverable:
        report["action"] = "refused"
        report["reason"] = (
            f"{det.protector} virtualizes code into custom VM bytecode; original "
            "instructions are not present in the binary and cannot be recovered to "
            "source by any tool. Not emitting fake source."
        )
        print(json.dumps(report, indent=2))
        return 2

    out = args.out or (args.binary + ".unpacked.exe")
    if det.protector == "UPX":
        ok, msg = unpack_upx(args.binary, out)
    else:
        ok, msg = unpack_dynamic_wine(args.binary, out, pe, det, args.timeout)

    report["action"] = "unpack"
    report["unpack_ok"] = ok
    report["unpack_detail"] = msg
    if ok:
        report["normalized_binary"] = out
        # verify the code section is now low-entropy
        nd = open(out, "rb").read()
        npe = parse_pe(nd)
        if npe:
            report["post_entropy"] = {
                s.name: round(s.entropy, 3) for s in npe.sections if s.name in det.encrypted_code_sections
            }
    print(json.dumps(report, indent=2))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
