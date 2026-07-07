"""Binary inventory extraction without target-specific assumptions."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .targets import TargetIdentity


class InventoryError(ValueError):
    """Raised when a binary is recognized but malformed or unsupported."""


@dataclass(frozen=True)
class BinaryView:
    path: Path
    data: bytes

    def u8(self, offset: int) -> int:
        self._check(offset, 1)
        return self.data[offset]

    def u16(self, offset: int) -> int:
        self._check(offset, 2)
        return struct.unpack_from("<H", self.data, offset)[0]

    def u32(self, offset: int) -> int:
        self._check(offset, 4)
        return struct.unpack_from("<I", self.data, offset)[0]

    def u64(self, offset: int) -> int:
        self._check(offset, 8)
        return struct.unpack_from("<Q", self.data, offset)[0]

    def bytes(self, offset: int, size: int) -> bytes:
        self._check(offset, size)
        return self.data[offset : offset + size]

    def c_string(self, offset: int, max_len: int = 4096) -> str:
        self._check(offset, 1)
        end = self.data.find(b"\0", offset, min(len(self.data), offset + max_len))
        if end < 0:
            end = min(len(self.data), offset + max_len)
        return self.data[offset:end].decode("utf-8", "replace")

    def _check(self, offset: int, size: int) -> None:
        if offset < 0 or offset + size > len(self.data):
            raise InventoryError(f"read outside file: offset={offset} size={size} file={self.path}")


def build_binary_inventory(target: TargetIdentity) -> dict[str, Any]:
    view = BinaryView(target.binary_path, target.binary_path.read_bytes())
    if target.format == "pe":
        return pe_inventory(target, view)
    if target.format == "elf":
        return elf_inventory(target, view)
    if target.format == "macho":
        return macho_inventory(target, view)
    return {
        "schema": "reconkit.binary-inventory.v1",
        "target": target.to_json(),
        "format": target.format,
        "status": "unsupported-format",
        "sections": [],
        "imports": [],
        "symbols": [],
        "codeRanges": [],
        "dataRanges": [],
    }


def pe_inventory(target: TargetIdentity, view: BinaryView) -> dict[str, Any]:
    pe_offset = view.u32(0x3C)
    if view.bytes(pe_offset, 4) != b"PE\0\0":
        raise InventoryError("missing PE signature")
    coff = pe_offset + 4
    machine = view.u16(coff)
    section_count = view.u16(coff + 2)
    timestamp = view.u32(coff + 4)
    optional_size = view.u16(coff + 16)
    characteristics = view.u16(coff + 18)
    optional = coff + 20
    magic = view.u16(optional)
    is_pe32_plus = magic == 0x20B
    if magic not in {0x10B, 0x20B}:
        raise InventoryError(f"unsupported PE optional-header magic: 0x{magic:x}")

    address_of_entry = view.u32(optional + 16)
    image_base = view.u64(optional + 24) if is_pe32_plus else view.u32(optional + 28)
    section_alignment = view.u32(optional + 32)
    file_alignment = view.u32(optional + 36)
    subsystem = view.u16(optional + 68)
    dll_characteristics = view.u16(optional + 70)
    data_dir_count = view.u32(optional + (108 if is_pe32_plus else 92))
    data_dir_offset = optional + (112 if is_pe32_plus else 96)
    data_dirs = []
    for index in range(min(data_dir_count, 16)):
        rva = view.u32(data_dir_offset + index * 8)
        size = view.u32(data_dir_offset + index * 8 + 4)
        data_dirs.append({"index": index, "rva": rva, "size": size})

    section_offset = optional + optional_size
    sections = []
    for index in range(section_count):
        off = section_offset + index * 40
        name = view.bytes(off, 8).split(b"\0", 1)[0].decode("utf-8", "replace")
        virtual_size = view.u32(off + 8)
        virtual_address = view.u32(off + 12)
        raw_size = view.u32(off + 16)
        raw_pointer = view.u32(off + 20)
        flags = view.u32(off + 36)
        sections.append(
            {
                "name": name,
                "virtualAddress": virtual_address,
                "virtualSize": virtual_size,
                "rawPointer": raw_pointer,
                "rawSize": raw_size,
                "characteristics": flags,
                "readable": bool(flags & 0x40000000),
                "writable": bool(flags & 0x80000000),
                "executable": bool(flags & 0x20000000),
            }
        )

    exports = pe_exports(view, sections, data_dirs[0] if len(data_dirs) > 0 else {"rva": 0, "size": 0})
    imports = pe_imports(view, sections, data_dirs[1] if len(data_dirs) > 1 else {"rva": 0, "size": 0}, is_pe32_plus)
    code_ranges = [
        {
            "name": section["name"],
            "rva": section["virtualAddress"],
            "size": section["virtualSize"] or section["rawSize"],
            "fileOffset": section["rawPointer"],
            "fileSize": section["rawSize"],
        }
        for section in sections
        if section["executable"]
    ]
    data_ranges = [
        {
            "name": section["name"],
            "rva": section["virtualAddress"],
            "size": section["virtualSize"] or section["rawSize"],
            "fileOffset": section["rawPointer"],
            "fileSize": section["rawSize"],
        }
        for section in sections
        if not section["executable"] and (section["readable"] or section["writable"])
    ]
    return {
        "schema": "reconkit.binary-inventory.v1",
        "target": target.to_json(),
        "format": "pe",
        "status": "complete",
        "machine": f"0x{machine:04x}",
        "timestamp": timestamp,
        "characteristics": characteristics,
        "entryRva": address_of_entry,
        "entryVa": image_base + address_of_entry,
        "imageBase": image_base,
        "sectionAlignment": section_alignment,
        "fileAlignment": file_alignment,
        "subsystem": subsystem,
        "dllCharacteristics": dll_characteristics,
        "dataDirectories": data_dirs,
        "sections": sections,
        "exports": exports,
        "imports": imports,
        "symbols": [],
        "codeRanges": code_ranges,
        "dataRanges": data_ranges,
        "summary": {
            "sections": len(sections),
            "codeRanges": len(code_ranges),
            "exports": len(exports),
            "imports": sum(len(item.get("symbols", [])) for item in imports),
            "importLibraries": len(imports),
        },
    }


def rva_to_offset(sections: list[dict[str, Any]], rva: int) -> int | None:
    for section in sections:
        start = int(section["virtualAddress"])
        size = max(int(section["virtualSize"]), int(section["rawSize"]))
        if start <= rva < start + size:
            return int(section["rawPointer"]) + (rva - start)
    return None


def pe_imports(view: BinaryView, sections: list[dict[str, Any]], import_dir: dict[str, int], is_pe32_plus: bool) -> list[dict[str, Any]]:
    if not import_dir.get("rva"):
        return []
    off = rva_to_offset(sections, int(import_dir["rva"]))
    if off is None:
        return []
    imports = []
    thunk_size = 8 if is_pe32_plus else 4
    ordinal_mask = 0x8000000000000000 if is_pe32_plus else 0x80000000
    index = 0
    while off + index * 20 + 20 <= len(view.data):
        desc = off + index * 20
        original_first_thunk = view.u32(desc)
        name_rva = view.u32(desc + 12)
        first_thunk = view.u32(desc + 16)
        if original_first_thunk == 0 and name_rva == 0 and first_thunk == 0:
            break
        name_offset = rva_to_offset(sections, name_rva)
        dll = view.c_string(name_offset) if name_offset is not None else f"rva_0x{name_rva:x}"
        thunk_rva = original_first_thunk or first_thunk
        thunk_off = rva_to_offset(sections, thunk_rva)
        symbols = []
        if thunk_off is not None:
            ordinal = 0
            while thunk_off + ordinal * thunk_size + thunk_size <= len(view.data):
                value = view.u64(thunk_off + ordinal * thunk_size) if is_pe32_plus else view.u32(thunk_off + ordinal * thunk_size)
                if value == 0:
                    break
                if value & ordinal_mask:
                    symbols.append({"kind": "ordinal", "ordinal": value & 0xFFFF})
                else:
                    hint_name_off = rva_to_offset(sections, int(value))
                    if hint_name_off is None:
                        symbols.append({"kind": "name", "name": f"rva_0x{value:x}"})
                    else:
                        symbols.append({"kind": "name", "hint": view.u16(hint_name_off), "name": view.c_string(hint_name_off + 2)})
                ordinal += 1
        imports.append({"library": dll, "descriptorRva": int(import_dir["rva"]) + index * 20, "firstThunkRva": first_thunk, "symbols": symbols})
        index += 1
    return imports


def pe_exports(view: BinaryView, sections: list[dict[str, Any]], export_dir: dict[str, int]) -> list[dict[str, Any]]:
    if not export_dir.get("rva"):
        return []
    off = rva_to_offset(sections, int(export_dir["rva"]))
    if off is None or off + 40 > len(view.data):
        return []
    ordinal_base = view.u32(off + 16)
    function_count = view.u32(off + 20)
    name_count = view.u32(off + 24)
    function_table_rva = view.u32(off + 28)
    name_table_rva = view.u32(off + 32)
    ordinal_table_rva = view.u32(off + 36)
    function_table_off = rva_to_offset(sections, function_table_rva)
    name_table_off = rva_to_offset(sections, name_table_rva)
    ordinal_table_off = rva_to_offset(sections, ordinal_table_rva)
    if function_table_off is None:
        return []

    functions: list[int] = []
    for index in range(function_count):
        entry_off = function_table_off + index * 4
        if entry_off + 4 > len(view.data):
            break
        functions.append(view.u32(entry_off))

    names_by_index: dict[int, dict[str, Any]] = {}
    if name_table_off is not None and ordinal_table_off is not None:
        for index in range(name_count):
            name_ptr_off = name_table_off + index * 4
            ordinal_off = ordinal_table_off + index * 2
            if name_ptr_off + 4 > len(view.data) or ordinal_off + 2 > len(view.data):
                break
            name_rva = view.u32(name_ptr_off)
            name_off = rva_to_offset(sections, name_rva)
            ordinal_index = view.u16(ordinal_off)
            if name_off is None:
                continue
            names_by_index[ordinal_index] = {
                "name": view.c_string(name_off),
                "nameRva": name_rva,
                "hint": index,
            }

    exports: list[dict[str, Any]] = []
    export_start = int(export_dir.get("rva") or 0)
    export_end = export_start + int(export_dir.get("size") or 0)
    for index, rva in enumerate(functions):
        if not rva:
            continue
        name_info = names_by_index.get(index, {})
        forwarded = export_start <= rva < export_end
        exports.append(
            {
                "name": name_info.get("name") or f"ordinal_{ordinal_base + index}",
                "ordinal": ordinal_base + index,
                "ordinalIndex": index,
                "rva": rva,
                "forwarded": forwarded,
                "forwarder": view.c_string(rva_to_offset(sections, rva)) if forwarded and rva_to_offset(sections, rva) is not None else None,
                "nameRva": name_info.get("nameRva"),
                "hint": name_info.get("hint"),
            }
        )
    return exports


def elf_inventory(target: TargetIdentity, view: BinaryView) -> dict[str, Any]:
    if view.bytes(0, 4) != b"\x7fELF":
        raise InventoryError("missing ELF magic")
    elf_class = view.u8(4)
    endian = view.u8(5)
    if endian != 1:
        raise InventoryError("big-endian ELF is not implemented")
    if elf_class == 1:
        return elf32_inventory(target, view)
    if elf_class == 2:
        return elf64_inventory(target, view)
    raise InventoryError(f"unsupported ELF class: {elf_class}")


def elf64_inventory(target: TargetIdentity, view: BinaryView) -> dict[str, Any]:
    e_type = view.u16(16)
    machine = view.u16(18)
    entry = view.u64(24)
    shoff = view.u64(40)
    shentsize = view.u16(58)
    shnum = view.u16(60)
    shstrndx = view.u16(62)
    sections = elf_sections(view, shoff, shentsize, shnum, shstrndx, is_64=True)
    symbols = elf_symbols(view, sections, is_64=True)
    return elf_common_inventory(target, e_type, machine, entry, sections, symbols)


def elf32_inventory(target: TargetIdentity, view: BinaryView) -> dict[str, Any]:
    e_type = view.u16(16)
    machine = view.u16(18)
    entry = view.u32(24)
    shoff = view.u32(32)
    shentsize = view.u16(46)
    shnum = view.u16(48)
    shstrndx = view.u16(50)
    sections = elf_sections(view, shoff, shentsize, shnum, shstrndx, is_64=False)
    symbols = elf_symbols(view, sections, is_64=False)
    return elf_common_inventory(target, e_type, machine, entry, sections, symbols)


def elf_sections(view: BinaryView, shoff: int, shentsize: int, shnum: int, shstrndx: int, *, is_64: bool) -> list[dict[str, Any]]:
    raw = []
    for index in range(shnum):
        off = shoff + index * shentsize
        if is_64:
            item = {
                "nameOffset": view.u32(off),
                "type": view.u32(off + 4),
                "flags": view.u64(off + 8),
                "address": view.u64(off + 16),
                "offset": view.u64(off + 24),
                "size": view.u64(off + 32),
                "link": view.u32(off + 40),
                "entrySize": view.u64(off + 56),
            }
        else:
            item = {
                "nameOffset": view.u32(off),
                "type": view.u32(off + 4),
                "flags": view.u32(off + 8),
                "address": view.u32(off + 12),
                "offset": view.u32(off + 16),
                "size": view.u32(off + 20),
                "link": view.u32(off + 24),
                "entrySize": view.u32(off + 36),
            }
        raw.append(item)
    names = b""
    if 0 <= shstrndx < len(raw):
        table = raw[shstrndx]
        names = view.bytes(int(table["offset"]), int(table["size"]))
    sections = []
    for index, item in enumerate(raw):
        name = read_string_from_table(names, int(item["nameOffset"]))
        flags = int(item["flags"])
        sections.append(
            {
                "index": index,
                "name": name,
                "type": int(item["type"]),
                "flags": flags,
                "address": int(item["address"]),
                "offset": int(item["offset"]),
                "size": int(item["size"]),
                "link": int(item["link"]),
                "entrySize": int(item["entrySize"]),
                "alloc": bool(flags & 0x2),
                "executable": bool(flags & 0x4),
                "writable": bool(flags & 0x1),
            }
        )
    return sections


def read_string_from_table(table: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(table):
        return ""
    end = table.find(b"\0", offset)
    if end < 0:
        end = len(table)
    return table[offset:end].decode("utf-8", "replace")


def elf_symbols(view: BinaryView, sections: list[dict[str, Any]], *, is_64: bool) -> list[dict[str, Any]]:
    symbols = []
    for section in sections:
        if section["type"] not in {2, 11}:  # SHT_SYMTAB, SHT_DYNSYM
            continue
        linked = sections[section["link"]] if 0 <= section["link"] < len(sections) else None
        strings = view.bytes(linked["offset"], linked["size"]) if linked else b""
        entry_size = int(section["entrySize"]) or (24 if is_64 else 16)
        if entry_size <= 0:
            continue
        count = int(section["size"]) // entry_size
        for index in range(count):
            off = int(section["offset"]) + index * entry_size
            if is_64:
                name_offset = view.u32(off)
                info = view.u8(off + 4)
                shndx = view.u16(off + 6)
                value = view.u64(off + 8)
                size = view.u64(off + 16)
            else:
                name_offset = view.u32(off)
                value = view.u32(off + 4)
                size = view.u32(off + 8)
                info = view.u8(off + 12)
                shndx = view.u16(off + 14)
            name = read_string_from_table(strings, name_offset)
            if not name and value == 0 and size == 0:
                continue
            symbols.append(
                {
                    "name": name,
                    "value": value,
                    "size": size,
                    "bind": info >> 4,
                    "type": info & 0xF,
                    "sectionIndex": shndx,
                    "table": section["name"],
                }
            )
    return symbols


def elf_common_inventory(target: TargetIdentity, e_type: int, machine: int, entry: int, sections: list[dict[str, Any]], symbols: list[dict[str, Any]]) -> dict[str, Any]:
    code_ranges = [
        {"name": section["name"], "address": section["address"], "offset": section["offset"], "size": section["size"]}
        for section in sections
        if section["executable"] and section["alloc"] and section["size"]
    ]
    data_ranges = [
        {"name": section["name"], "address": section["address"], "offset": section["offset"], "size": section["size"]}
        for section in sections
        if section["alloc"] and not section["executable"] and section["size"]
    ]
    function_symbols = [sym for sym in symbols if sym["type"] == 2 and sym["sectionIndex"] != 0 and sym["value"] != 0]
    imported_symbols = [sym for sym in symbols if sym["table"] == ".dynsym" and sym["sectionIndex"] == 0 and sym["name"]]
    imports = [{"library": "dynamic-symbol-table", "symbols": [{"kind": "name", "name": sym["name"]} for sym in imported_symbols]}]
    return {
        "schema": "reconkit.binary-inventory.v1",
        "target": target.to_json(),
        "format": "elf",
        "status": "complete",
        "type": e_type,
        "machine": f"0x{machine:04x}",
        "entryVa": entry,
        "sections": sections,
        "imports": imports,
        "symbols": symbols,
        "codeRanges": code_ranges,
        "dataRanges": data_ranges,
        "summary": {
            "sections": len(sections),
            "codeRanges": len(code_ranges),
            "dataRanges": len(data_ranges),
            "imports": len(imported_symbols),
            "symbols": len(symbols),
            "functionSymbols": len(function_symbols),
            "dynamicSymbols": sum(1 for sym in symbols if sym["table"] == ".dynsym"),
        },
    }


def macho_inventory(target: TargetIdentity, view: BinaryView) -> dict[str, Any]:
    magic = view.bytes(0, 4)
    if magic not in {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"}:
        raise InventoryError("missing Mach-O magic")
    endian = "<" if magic in {b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"} else ">"
    is_64 = magic in {b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"}
    header_size = 32 if is_64 else 28
    cputype = macho_i32(view, 4, endian)
    cpusubtype = macho_i32(view, 8, endian)
    filetype = macho_u32(view, 12, endian)
    ncmds = macho_u32(view, 16, endian)
    sizeofcmds = macho_u32(view, 20, endian)
    flags = macho_u32(view, 24, endian)

    sections: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    symtab: dict[str, int] | None = None
    entryoff: int | None = None
    cursor = header_size
    section_index = 1
    for _index in range(ncmds):
        if cursor + 8 > len(view.data):
            break
        cmd = macho_u32(view, cursor, endian)
        cmdsize = macho_u32(view, cursor + 4, endian)
        if cmdsize < 8 or cursor + cmdsize > len(view.data):
            break
        if is_64 and cmd == 0x19:  # LC_SEGMENT_64
            segment, section_rows = macho_segment_64(view, cursor, endian, section_index)
            segments.append(segment)
            sections.extend(section_rows)
            section_index += len(section_rows)
        elif not is_64 and cmd == 0x1:  # LC_SEGMENT
            segment, section_rows = macho_segment_32(view, cursor, endian, section_index)
            segments.append(segment)
            sections.extend(section_rows)
            section_index += len(section_rows)
        elif cmd == 0x2:  # LC_SYMTAB
            symtab = {
                "symoff": macho_u32(view, cursor + 8, endian),
                "nsyms": macho_u32(view, cursor + 12, endian),
                "stroff": macho_u32(view, cursor + 16, endian),
                "strsize": macho_u32(view, cursor + 20, endian),
            }
        elif cmd == 0x80000028 and is_64:  # LC_MAIN
            entryoff = macho_u64(view, cursor + 8, endian)
        cursor += cmdsize

    symbols = macho_symbols(view, symtab, sections, is_64=is_64, endian=endian)
    annotate_macho_function_sizes(symbols, sections)
    code_ranges = [
        {
            "name": f"{section['segmentName']},{section['name']}",
            "rva": section["address"],
            "address": section["address"],
            "size": section["size"],
            "fileOffset": section["offset"],
            "fileSize": section["size"],
        }
        for section in sections
        if section["executable"] and section["size"]
    ]
    data_ranges = [
        {
            "name": f"{section['segmentName']},{section['name']}",
            "rva": section["address"],
            "address": section["address"],
            "size": section["size"],
            "fileOffset": section["offset"],
            "fileSize": section["size"],
        }
        for section in sections
        if not section["executable"] and section["size"]
    ]
    function_symbols = [sym for sym in symbols if sym.get("type") == 2]
    return {
        "schema": "reconkit.binary-inventory.v1",
        "target": target.to_json(),
        "format": "macho",
        "status": "complete",
        "class": "64" if is_64 else "32",
        "endianness": "little" if endian == "<" else "big",
        "cpuType": cputype,
        "cpuSubtype": cpusubtype,
        "fileType": filetype,
        "flags": flags,
        "entryOff": entryoff,
        "entryVa": macho_entry_address_from_offset(entryoff, sections) if entryoff is not None else None,
        "segments": segments,
        "sections": sections,
        "imports": [],
        "symbols": symbols,
        "codeRanges": code_ranges,
        "dataRanges": data_ranges,
        "summary": {
            "segments": len(segments),
            "sections": len(sections),
            "codeRanges": len(code_ranges),
            "dataRanges": len(data_ranges),
            "symbols": len(symbols),
            "functionSymbols": len(function_symbols),
            "loadCommands": ncmds,
            "loadCommandsSize": sizeofcmds,
        },
    }


def macho_segment_64(view: BinaryView, offset: int, endian: str, first_section_index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    segname = macho_fixed_string(view.bytes(offset + 8, 16))
    vmaddr = macho_u64(view, offset + 24, endian)
    vmsize = macho_u64(view, offset + 32, endian)
    fileoff = macho_u64(view, offset + 40, endian)
    filesize = macho_u64(view, offset + 48, endian)
    maxprot = macho_u32(view, offset + 56, endian)
    initprot = macho_u32(view, offset + 60, endian)
    nsects = macho_u32(view, offset + 64, endian)
    flags = macho_u32(view, offset + 68, endian)
    sections = []
    section_offset = offset + 72
    for index in range(nsects):
        off = section_offset + index * 80
        sections.append(macho_section_64(view, off, endian, segname, first_section_index + index, initprot))
    return {
        "name": segname,
        "vmaddr": vmaddr,
        "vmsize": vmsize,
        "fileoff": fileoff,
        "filesize": filesize,
        "maxprot": maxprot,
        "initprot": initprot,
        "sections": nsects,
        "flags": flags,
    }, sections


def macho_segment_32(view: BinaryView, offset: int, endian: str, first_section_index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    segname = macho_fixed_string(view.bytes(offset + 8, 16))
    vmaddr = macho_u32(view, offset + 24, endian)
    vmsize = macho_u32(view, offset + 28, endian)
    fileoff = macho_u32(view, offset + 32, endian)
    filesize = macho_u32(view, offset + 36, endian)
    maxprot = macho_u32(view, offset + 40, endian)
    initprot = macho_u32(view, offset + 44, endian)
    nsects = macho_u32(view, offset + 48, endian)
    flags = macho_u32(view, offset + 52, endian)
    sections = []
    section_offset = offset + 56
    for index in range(nsects):
        off = section_offset + index * 68
        sections.append(macho_section_32(view, off, endian, segname, first_section_index + index, initprot))
    return {
        "name": segname,
        "vmaddr": vmaddr,
        "vmsize": vmsize,
        "fileoff": fileoff,
        "filesize": filesize,
        "maxprot": maxprot,
        "initprot": initprot,
        "sections": nsects,
        "flags": flags,
    }, sections


def macho_section_64(view: BinaryView, offset: int, endian: str, segment_name: str, index: int, initprot: int) -> dict[str, Any]:
    flags = macho_u32(view, offset + 64, endian)
    return {
        "index": index,
        "name": macho_fixed_string(view.bytes(offset, 16)),
        "segmentName": macho_fixed_string(view.bytes(offset + 16, 16)) or segment_name,
        "address": macho_u64(view, offset + 32, endian),
        "size": macho_u64(view, offset + 40, endian),
        "offset": macho_u32(view, offset + 48, endian),
        "align": macho_u32(view, offset + 52, endian),
        "relocationOffset": macho_u32(view, offset + 56, endian),
        "relocationCount": macho_u32(view, offset + 60, endian),
        "flags": flags,
        "executable": macho_section_executable(flags, initprot),
        "writable": bool(initprot & 0x2),
        "readable": bool(initprot & 0x1),
    }


def macho_section_32(view: BinaryView, offset: int, endian: str, segment_name: str, index: int, initprot: int) -> dict[str, Any]:
    flags = macho_u32(view, offset + 56, endian)
    return {
        "index": index,
        "name": macho_fixed_string(view.bytes(offset, 16)),
        "segmentName": macho_fixed_string(view.bytes(offset + 16, 16)) or segment_name,
        "address": macho_u32(view, offset + 32, endian),
        "size": macho_u32(view, offset + 36, endian),
        "offset": macho_u32(view, offset + 40, endian),
        "align": macho_u32(view, offset + 44, endian),
        "relocationOffset": macho_u32(view, offset + 48, endian),
        "relocationCount": macho_u32(view, offset + 52, endian),
        "flags": flags,
        "executable": macho_section_executable(flags, initprot),
        "writable": bool(initprot & 0x2),
        "readable": bool(initprot & 0x1),
    }


def macho_symbols(view: BinaryView, symtab: dict[str, int] | None, sections: list[dict[str, Any]], *, is_64: bool, endian: str) -> list[dict[str, Any]]:
    if not symtab:
        return []
    symoff = int(symtab["symoff"])
    nsyms = int(symtab["nsyms"])
    stroff = int(symtab["stroff"])
    strsize = int(symtab["strsize"])
    if stroff < 0 or strsize < 0 or stroff + strsize > len(view.data):
        strings = b""
    else:
        strings = view.bytes(stroff, strsize)
    entry_size = 16 if is_64 else 12
    symbols = []
    section_by_index = {int(section["index"]): section for section in sections}
    for index in range(nsyms):
        off = symoff + index * entry_size
        if off + entry_size > len(view.data):
            break
        strx = macho_u32(view, off, endian)
        n_type = view.u8(off + 4)
        n_sect = view.u8(off + 5)
        n_desc = macho_u16(view, off + 6, endian)
        value = macho_u64(view, off + 8, endian) if is_64 else macho_u32(view, off + 8, endian)
        name = read_string_from_table(strings, strx)
        section = section_by_index.get(n_sect)
        is_section_symbol = (n_type & 0x0E) == 0x0E and not (n_type & 0xE0)
        is_function = bool(is_section_symbol and section and section.get("executable"))
        symbols.append(
            {
                "name": name,
                "value": value,
                "size": 0,
                "bind": 1 if n_type & 0x01 else 0,
                "type": 2 if is_function else 0,
                "rawType": n_type,
                "description": n_desc,
                "sectionIndex": n_sect,
                "section": section.get("name") if section else None,
                "segment": section.get("segmentName") if section else None,
                "table": "LC_SYMTAB",
            }
        )
    return symbols


def annotate_macho_function_sizes(symbols: list[dict[str, Any]], sections: list[dict[str, Any]]) -> None:
    section_by_index = {int(section["index"]): section for section in sections}
    functions = [sym for sym in symbols if sym.get("type") == 2 and int(sym.get("sectionIndex") or 0) in section_by_index]
    functions.sort(key=lambda sym: (int(sym.get("sectionIndex") or 0), int(sym.get("value") or 0), str(sym.get("name") or "")))
    for index, sym in enumerate(functions):
        section = section_by_index[int(sym["sectionIndex"])]
        section_end = int(section["address"]) + int(section["size"])
        next_address = section_end
        for later in functions[index + 1 :]:
            if int(later.get("sectionIndex") or 0) == int(sym.get("sectionIndex") or 0):
                next_address = int(later.get("value") or section_end)
                break
        size = max(0, next_address - int(sym.get("value") or 0))
        sym["size"] = size


def macho_entry_address_from_offset(entryoff: int | None, sections: list[dict[str, Any]]) -> int | None:
    if entryoff is None:
        return None
    for section in sections:
        offset = int(section.get("offset") or 0)
        size = int(section.get("size") or 0)
        if offset <= entryoff < offset + size:
            return int(section.get("address") or 0) + (entryoff - offset)
    return None


def macho_section_executable(flags: int, initprot: int) -> bool:
    return bool(flags & 0x80000000 or flags & 0x00000400 or initprot & 0x4)


def macho_fixed_string(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("utf-8", "replace")


def macho_u16(view: BinaryView, offset: int, endian: str) -> int:
    view._check(offset, 2)
    return struct.unpack_from(f"{endian}H", view.data, offset)[0]


def macho_u32(view: BinaryView, offset: int, endian: str) -> int:
    view._check(offset, 4)
    return struct.unpack_from(f"{endian}I", view.data, offset)[0]


def macho_i32(view: BinaryView, offset: int, endian: str) -> int:
    view._check(offset, 4)
    return struct.unpack_from(f"{endian}i", view.data, offset)[0]


def macho_u64(view: BinaryView, offset: int, endian: str) -> int:
    view._check(offset, 8)
    return struct.unpack_from(f"{endian}Q", view.data, offset)[0]


def write_inventory(path: Path, inventory: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
