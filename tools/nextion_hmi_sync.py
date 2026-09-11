#!/usr/bin/env python3
"""Sync tracked Nextion page layouts into the shipped .HMI files.

This tool intentionally edits existing .HMI containers in place instead of
trying to rebuild the full Nextion container directory from scratch.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List


REPO_ROOT = Path(__file__).resolve().parent.parent

VARIANTS = {
    "nspanel_eu": (
        REPO_ROOT / "hmi" / "nspanel_eu.HMI",
        REPO_ROOT / "hmi" / "dev" / "nspanel_eu_code",
    ),
    "nspanel_us": (
        REPO_ROOT / "hmi" / "nspanel_us.HMI",
        REPO_ROOT / "hmi" / "dev" / "nspanel_us_code",
    ),
    "nspanel_us_land": (
        REPO_ROOT / "hmi" / "nspanel_us_land.HMI",
        REPO_ROOT / "hmi" / "dev" / "nspanel_us_land_code",
    ),
    "nspanel_CJK_eu": (
        REPO_ROOT / "hmi" / "nspanel_CJK_eu.HMI",
        REPO_ROOT / "hmi" / "dev" / "nspanel_CJK_eu_code",
    ),
    "nspanel_CJK_us": (
        REPO_ROOT / "hmi" / "nspanel_CJK_us.HMI",
        REPO_ROOT / "hmi" / "dev" / "nspanel_CJK_us_code",
    ),
    "nspanel_CJK_us_land": (
        REPO_ROOT / "hmi" / "nspanel_CJK_us_land.HMI",
        REPO_ROOT / "hmi" / "dev" / "nspanel_CJK_us_land_code",
    ),
}

SOURCE_LAYOUT_FIELDS = {
    "x coordinate": "x",
    "y coordinate": "y",
    "Width": "w",
    "Height": "h",
}

STRING_KEYS = {"objname", "txt", "path", "from", "val0", "val1"}


def _build_crc_table() -> List[int]:
    poly = 0x04C11DB7
    table: List[int] = []
    for n in range(256):
        c = n << 24
        for _ in range(8):
            c = (((c << 1) ^ poly) & 0xFFFFFFFF) if (c & 0x80000000) else ((c << 1) & 0xFFFFFFFF)
        table.append(c)
    return table


CRC_TABLE = _build_crc_table()

STAMP_CONSTANTS = {
    112: 0xAF28780C,
    128: 0xF5BA4653,
    224: 0x1AFB582C,
    256: 0xD72C4AC5,
    304: 0xE59149FB,
    336: 0x956DAF76,
    368: 0x4BB7A997,
    769: 0x19139AFD,
    1290: 0x44C50407,
    1669: 0x72DA8717,
    2304: 0xB0EE46F9,
    2368: 0x113DDB18,
    4517: 0x5C3175E0,
    4890: 0x21AAF2E9,
    5423: 0x20B83357,
    13289: 0xF0BCAD06,
    13291: 0x73096521,
    14916: 0x507EB40E,
    15839: 0xABF08E70,
    16313: 0x4653FF2F,
    16693: 0xA0FFD7E4,
    16976: 0xF7E073AB,
    28352: 0x71C0F123,
    28397: 0xB52C8C07,
    31196: 0x3BAAE365,
    32875: 0x5E6A02E3,
    32884: 0x1A9A01B6,
    33240: 0xB5E89ED0,
    34896: 0x1A97DF56,
    38455: 0xC9C3BA4C,
    38467: 0x2B5A1AAD,
}


@dataclass
class DirectoryEntry:
    name: str
    off: int
    size: int
    flags: int

    @property
    def stale(self) -> bool:
        return bool(self.flags & 1)


def read_u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def read_u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def step_crc(c: int, b: int) -> int:
    return (((c << 8) & 0xFFFFFFFF) ^ CRC_TABLE[((c >> 24) ^ b) & 0xFF]) & 0xFFFFFFFF


def block_core(block: bytes) -> int:
    c = 0
    for b in block[4:]:
        c = step_crc(c, 0)
        c = step_crc(c, 0)
        c = step_crc(c, 0)
        c = step_crc(c, b)
    for _ in range(40):
        c = step_crc(c, 0)
    return c


def ensure_stamp_constant(block: bytes) -> int:
    if len(block) not in STAMP_CONSTANTS:
        STAMP_CONSTANTS[len(block)] = read_u32(block, 0) ^ block_core(block)
    return STAMP_CONSTANTS[len(block)]


def reseal(block: bytearray, original: bytes) -> None:
    constant = ensure_stamp_constant(original)
    struct.pack_into("<I", block, 0, 0)
    struct.pack_into("<I", block, 0, (block_core(block) ^ constant) & 0xFFFFFFFF)


def read_directory(buf: bytes) -> List[DirectoryEntry]:
    count = read_u32(buf, 0)
    entries: List[DirectoryEntry] = []
    for i in range(count):
        off = 4 + i * 28
        if off + 28 > len(buf):
            break
        name = buf[off : off + 16].split(b"\0", 1)[0].decode("latin1")
        entries.append(
            DirectoryEntry(
                name=name,
                off=read_u32(buf, off + 16),
                size=read_u32(buf, off + 20),
                flags=read_u32(buf, off + 24),
            )
        )
    return entries


def parse_source_layouts(code_dir: Path) -> Dict[str, Dict[str, Dict[str, int]]]:
    pages: Dict[str, Dict[str, Dict[str, int]]] = {}
    for path in sorted(code_dir.glob("*.txt")):
        current_name = None
        current_section = None
        object_layouts: Dict[str, Dict[str, int]] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if raw_line and not raw_line.startswith(" "):
                current_name = raw_line.rsplit(" ", 1)[-1]
                current_section = None
                object_layouts.setdefault(current_name, {})
                continue
            stripped = raw_line.strip()
            if stripped == "Attributes":
                current_section = "attributes"
                continue
            if stripped == "Events":
                current_section = "events"
                continue
            if current_section != "attributes" or current_name is None or ":" not in stripped:
                continue
            label, value = (part.strip() for part in stripped.split(":", 1))
            attr = SOURCE_LAYOUT_FIELDS.get(label)
            if not attr:
                continue
            try:
                object_layouts[current_name][attr] = int(value)
            except ValueError:
                continue
        if object_layouts:
            pages[path.stem] = object_layouts
    return pages


def decode_value(key: str, raw: bytes):
    if key in STRING_KEYS:
        return raw.rstrip(b"\0").decode("latin1")
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2:
        return read_u16(raw, 0)
    if len(raw) == 4:
        return read_u32(raw, 0)
    return raw


def encode_value(value: int, old_len: int) -> bytes:
    out = bytearray(old_len)
    if old_len == 1:
        out[0] = value & 0xFF
    elif old_len == 2:
        struct.pack_into("<H", out, 0, value & 0xFFFF)
    else:
        struct.pack_into("<I", out, 0, value & 0xFFFFFFFF)
    return bytes(out)


def parse_page_block(block: bytes):
    header_size = read_u32(block, 8)
    object_count = read_u32(block, 12)
    page_name = block[24:40].split(b"\0", 1)[0].decode("latin1")
    objects = []
    for index in range(object_count):
        table_off = header_size + index * 12
        obj_off = read_u32(block, table_off)
        obj_len = read_u32(block, table_off + 4)
        body = block[header_size + obj_off : header_size + obj_off + obj_len]
        items = []
        pos = 0
        while pos + 4 <= len(body):
            length = read_u32(body, pos)
            if length == 0:
                items.append({"kind": "end"})
                pos += 4
                continue
            record = body[pos + 4 : pos + 4 + length]
            if length >= 16:
                key = record[:16].split(b"\0", 1)[0].decode("latin1")
                raw = record[16:]
                items.append({"kind": "attr", "key": key, "raw": raw, "value": decode_value(key, raw)})
            else:
                items.append({"kind": "mark", "text": record.decode("latin1")})
            pos += 4 + length
        objects.append(items)
    return page_name, bytes(block[:header_size]), objects


def build_page_block(page_name: str, header: bytes, objects: list) -> bytearray:
    object_bodies = []
    for items in objects:
        parts = []
        for item in items:
            if item["kind"] == "end":
                parts.append(b"\0\0\0\0")
            elif item["kind"] == "mark":
                text = item["text"].encode("latin1")
                parts.append(struct.pack("<I", len(text)))
                parts.append(text)
            else:
                key = item["key"].encode("latin1")
                padded_key = key + b"\0" * (16 - len(key))
                parts.append(struct.pack("<I", 16 + len(item["raw"])))
                parts.append(padded_key)
                parts.append(item["raw"])
        object_bodies.append(b"".join(parts))

    table = bytearray(len(objects) * 12)
    next_off = len(table)
    for index, body in enumerate(object_bodies):
        struct.pack_into("<III", table, index * 12, next_off, len(body), 0)
        next_off += len(body)

    out = bytearray(header)
    struct.pack_into("<I", out, 8, 56)
    struct.pack_into("<I", out, 12, len(objects))
    out[24:40] = b"\0" * 16
    out[24 : 24 + len(page_name)] = page_name.encode("latin1")
    out.extend(table)
    for body in object_bodies:
        out.extend(body)
    struct.pack_into("<I", out, 4, len(out))
    return out


def apply_layout_updates(page_name: str, objects: list, source_objects: Dict[str, Dict[str, int]]) -> int:
    changed = 0
    for items in objects:
        objname = None
        attrs = {}
        for item in items:
            if item["kind"] == "attr":
                attrs[item["key"]] = item
                if item["key"] == "objname":
                    objname = item["value"]
        if objname is None and page_name in source_objects:
            objname = page_name
        if objname not in source_objects:
            continue

        updates = dict(source_objects[objname])
        if {"x", "w"}.issubset(updates) and "endx" in attrs:
            updates["endx"] = updates["x"] + updates["w"] - 1
        if {"y", "h"}.issubset(updates) and "endy" in attrs:
            updates["endy"] = updates["y"] + updates["h"] - 1

        for key, new_value in updates.items():
            item = attrs.get(key)
            if item is None or item["value"] == new_value:
                continue
            item["raw"] = encode_value(new_value, len(item["raw"]))
            item["value"] = new_value
            changed += 1
    return changed


def sync_variant(variant: str, check_only: bool) -> str:
    hmi_path, code_dir = VARIANTS[variant]
    source_pages = parse_source_layouts(code_dir)
    data = bytearray(hmi_path.read_bytes())
    changed_pages = 0
    changed_fields = 0

    for entry in read_directory(data):
        if entry.stale or not entry.name.endswith(".pa"):
            continue
        if entry.off + entry.size > len(data):
            raise RuntimeError(f"{hmi_path.name}:{entry.name} points outside the file")

        original = bytes(data[entry.off : entry.off + entry.size])
        if len(original) < 56 or read_u32(original, 4) != entry.size or read_u32(original, 8) != 56:
            continue

        page_name, header, objects = parse_page_block(original)
        source_objects = source_pages.get(page_name)
        if not source_objects:
            continue

        page_field_changes = apply_layout_updates(page_name, objects, source_objects)
        if page_field_changes == 0:
            continue

        rebuilt = build_page_block(page_name, header, objects)
        if len(rebuilt) != entry.size:
            raise RuntimeError(
                f"{hmi_path.name}:{page_name} changed size {entry.size}->{len(rebuilt)}; "
                "this tool only supports length-preserving updates"
            )
        reseal(rebuilt, original)
        data[entry.off : entry.off + entry.size] = rebuilt
        changed_pages += 1
        changed_fields += page_field_changes

    if not check_only and changed_pages:
        hmi_path.write_bytes(data)

    mode = "check" if check_only else "sync"
    if changed_pages:
        return f"{mode}: {variant}: {changed_pages} page(s), {changed_fields} field(s)"
    return f"{mode}: {variant}: up to date"


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=sorted(VARIANTS), action="append", help="Variant(s) to process")
    parser.add_argument("--all", action="store_true", help="Process all known variants")
    parser.add_argument("--check", action="store_true", help="Report required updates without writing files")
    parser.add_argument("--list-variants", action="store_true", help="List the known HMI/code-dir mappings")
    args = parser.parse_args(list(argv))
    if not args.list_variants and not args.all and not args.variant:
        parser.error("choose --variant, --all, or --list-variants")
    return args


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    if args.list_variants:
        for name, (hmi_path, code_dir) in sorted(VARIANTS.items()):
            print(f"{name}:")
            print(f"  HMI:  {hmi_path}")
            print(f"  text: {code_dir}")
        return 0

    variants = sorted(VARIANTS) if args.all else args.variant
    try:
        for variant in variants:
            print(sync_variant(variant, check_only=args.check))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
