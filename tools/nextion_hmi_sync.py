#!/usr/bin/env python3
"""Sync tracked Nextion page layouts into the shipped .HMI files.

This tool intentionally edits existing .HMI containers in place instead of
trying to rebuild the full Nextion container directory from scratch.
"""

from __future__ import annotations

import argparse
import re
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
    "Max. Text Size": "txt_maxl",
}

STRING_KEYS = {"objname", "txt", "path", "from", "val0", "val1"}

# Event headers in the text exports -> event marks in the .HMI page blocks.
SOURCE_EVENTS = {
    "Preinitialize Event": "codesload",
    "Postinitialize Event": "codesloadend",
    "Touch Press Event": "codesdown",
    "Touch Release Event": "codesup",
    "Page Exit Event": "codesunload",
    "Timer Event": "codestimer",
    "Touch Move Event": "codesslide",
}
EVENT_HEADER_INDENT = " " * 8
CODE_INDENT = " " * 12
CODE_MARK_RE = re.compile(r"(codes[a-z]+)-(\d+)")
ESCAPE_RE = re.compile(rb"\\x([0-9a-fA-F]{2})")

# The stamp of a page block can only be recomputed for block lengths that are
# already known, so code changes are balanced back to the original length with a
# comment line in the page's Page Exit event (comments are not compiled into the TFT).
PAD_EVENT = "codesunload"
PAD_PREFIX = b"//hmi_sync pad"

# Pictures: "<n>.is" holds the imported PNG, "<n>.i" the converted RGB565 data used for the TFT.
# Tracked replacements live in hmi/dev/<variant>_pictures/<picture id>.png.
PICTURE_MAGIC = b"\x0a\x64\x01\x03"
PICTURE_SOURCE_MAGIC = b"\x0a\x64\x01\x01"
PICTURE_HEADER = 24
PICTURE_SOURCE_HEADER = 27
PICTURE_RAW_DATA_HEADER = 20  # mode 0 (uncompressed) + zero padding before the pixels
DIRECTORY_ENTRY = 28


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


def encode_code_line(line: str) -> bytes:
    """Convert a text-export code line (after the base indent) to the bytes stored in the .HMI.

    The exports indent 4 spaces per level where the .HMI stores 2, and write raw bytes as ``\\xNN``.
    """
    content = line.lstrip(" ")
    indent = " " * ((len(line) - len(content)) // 2)
    return ESCAPE_RE.sub(lambda m: bytes([int(m.group(1), 16)]), (indent + content.rstrip()).encode("utf-8"))


def parse_source_code(code_dir: Path) -> Dict[str, Dict[str, Dict[str, List[bytes]]]]:
    """Return {page: {object: {event mark: [code lines]}}} from the tracked text exports."""
    pages: Dict[str, Dict[str, Dict[str, List[bytes]]]] = {}
    for path in sorted(code_dir.glob("*.txt")):
        if path.stem == "Program.s":
            continue
        objects: Dict[str, Dict[str, List[bytes]]] = {}
        current_name = None
        current_event = None
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if raw_line and not raw_line.startswith(" "):
                current_name = raw_line.rsplit(" ", 1)[-1]
                current_event = None
                continue
            if raw_line.startswith(EVENT_HEADER_INDENT) and not raw_line.startswith(EVENT_HEADER_INDENT + " "):
                mark = SOURCE_EVENTS.get(raw_line.strip())
                if mark and current_name is not None:
                    current_event = objects.setdefault(current_name, {}).setdefault(mark, [])
                else:
                    current_event = None
                continue
            if current_event is None:
                continue
            if raw_line.startswith(CODE_INDENT):
                current_event.append(encode_code_line(raw_line[len(CODE_INDENT):]))
            elif raw_line.strip() == "":
                current_event.append(b"")
            else:
                current_event = None
        for events in objects.values():
            for lines in events.values():
                while lines and lines[-1] == b"":
                    lines.pop()
        pages[path.stem] = objects
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
        code_left = 0
        while pos + 4 <= len(body):
            length = read_u32(body, pos)
            if length == 0:
                items.append({"kind": "end"})
                pos += 4
                continue
            record = body[pos + 4 : pos + 4 + length]
            if code_left:
                items.append({"kind": "code", "raw": record})
                code_left -= 1
            elif length >= 16:
                key = record[:16].split(b"\0", 1)[0].decode("latin1")
                raw = record[16:]
                items.append({"kind": "attr", "key": key, "raw": raw, "value": decode_value(key, raw)})
            else:
                text = record.decode("latin1")
                items.append({"kind": "mark", "text": text})
                match = CODE_MARK_RE.fullmatch(text)
                if match:
                    code_left = int(match.group(2))
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
            elif item["kind"] == "code":
                parts.append(struct.pack("<I", len(item["raw"])))
                parts.append(item["raw"])
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


def object_name(items: list, page_name: str) -> str:
    for item in items:
        if item["kind"] == "attr" and item["key"] == "objname":
            return item["value"]
    return page_name


def is_pad(line: bytes) -> bool:
    return line.strip().startswith(PAD_PREFIX)


def normalize_code(lines: List[bytes]) -> List[bytes]:
    # Indentation may be trimmed by balance_length(), and blank lines cannot be stored
    return [line.strip() for line in lines if line.strip() and not is_pad(line)]


def split_events(items: list):
    """Yield (mark index, event mark, code line count) for each event in an object."""
    for index, item in enumerate(items):
        if item["kind"] == "mark":
            match = CODE_MARK_RE.fullmatch(item["text"])
            if match:
                yield index, match.group(1), int(match.group(2))


def set_event_lines(items: list, mark_index: int, event: str, old_count: int, lines: List[bytes]) -> None:
    items[mark_index + 1 : mark_index + 1 + old_count] = [{"kind": "code", "raw": line} for line in lines]
    items[mark_index] = {"kind": "mark", "text": f"{event}-{len(lines)}"}


def apply_code_updates(page_name: str, objects: list, source_objects: Dict[str, Dict[str, List[bytes]]]) -> List[str]:
    changed: List[str] = []
    seen_objects = set()
    for items in objects:
        name = object_name(items, page_name)
        seen_objects.add(name)
        source_events = source_objects.get(name, {})
        seen_events = set()
        for mark_index, event, count in list(split_events(items)):
            seen_events.add(event)
            current = [item["raw"] for item in items[mark_index + 1 : mark_index + 1 + count]]
            wanted = [line for line in source_events.get(event, []) if line.strip()]
            if normalize_code(current) == normalize_code(wanted):
                continue
            set_event_lines(items, mark_index, event, count, wanted + [line for line in current if is_pad(line)])
            changed.append(f"{name}.{event}")
        missing = [event for event, lines in source_events.items() if event not in seen_events and normalize_code(lines)]
        if missing:
            raise RuntimeError(f"{page_name}.{name}: event(s) {', '.join(missing)} not present in the .HMI object")
    missing_objects = [
        name for name, events in source_objects.items()
        if name not in seen_objects and any(normalize_code(lines) for lines in events.values())
    ]
    if missing_objects:
        raise RuntimeError(f"{page_name}: object(s) {', '.join(missing_objects)} not present in the .HMI page")
    return changed


def set_pad(page_items: list, text_len) -> None:
    """Replace the pad line in the page's Page Exit event (text_len=None removes it)."""
    for mark_index, event, count in split_events(page_items):
        if event != PAD_EVENT:
            continue
        lines = [item["raw"] for item in page_items[mark_index + 1 : mark_index + 1 + count] if not is_pad(item["raw"])]
        if text_len is not None:
            lines.append(PAD_PREFIX + b"-" * (text_len - len(PAD_PREFIX)))
        set_event_lines(page_items, mark_index, event, count, lines)
        return
    raise RuntimeError("page object has no Page Exit event to hold the size pad")


def pad_len(page_items: list):
    for mark_index, event, count in split_events(page_items):
        if event == PAD_EVENT:
            for item in page_items[mark_index + 1 : mark_index + 1 + count]:
                if is_pad(item["raw"]):
                    return len(item["raw"])
    return None


def strip_indentation(objects: list, amount: int) -> None:
    """Remove `amount` bytes of leading whitespace from code lines, starting with the last ones."""
    for items in reversed(objects):
        for item in reversed(items):
            if amount == 0:
                return
            if item["kind"] != "code" or is_pad(item["raw"]):
                continue
            indent = len(item["raw"]) - len(item["raw"].lstrip(b" \t"))
            take = min(indent, amount)
            item["raw"] = item["raw"][take:]
            amount -= take
    if amount:
        raise RuntimeError(f"not enough indentation to keep the block size ({amount} byte(s) short)")


def balance_length(page_name: str, header: bytes, objects: list, target: int) -> None:
    """Bring the rebuilt page block back to `target` bytes with the pad line and indentation trimming."""
    try:
        _balance_length(page_name, header, objects, target)
    except RuntimeError as exc:
        raise RuntimeError(f"{page_name}: {exc}; make the text change size-neutral for this page") from exc


def _balance_length(page_name: str, header: bytes, objects: list, target: int) -> None:
    page_items = next(items for items in objects if object_name(items, page_name) == page_name)
    set_pad(page_items, None)
    min_pad = 4 + len(PAD_PREFIX)  # record length field + prefix
    for _ in range(8):
        diff = len(build_page_block(page_name, header, objects)) - target
        if diff == 0:
            return
        pad = pad_len(page_items)
        if diff > 0:
            if pad is not None and pad - diff >= len(PAD_PREFIX):
                set_pad(page_items, pad - diff)
            elif pad is not None:
                set_pad(page_items, None)
            else:
                strip_indentation(objects, diff)
        elif pad is not None:
            set_pad(page_items, pad - diff)
        elif -diff >= min_pad:
            set_pad(page_items, -diff - 4)
        else:
            strip_indentation(objects, min_pad + diff)
    raise RuntimeError("could not balance the block size")


def live_entries(data: bytes) -> Dict[str, DirectoryEntry]:
    return {entry.name: entry for entry in read_directory(data) if not entry.stale}


def picture_names(data: bytes) -> List[str]:
    """Return the resource base names of the pictures, indexed by picture id (from main.HMI)."""
    main = live_entries(data)["main.HMI"]
    block = data[main.off : main.off + main.size]
    start, count = read_u32(block, 24), read_u32(block, 28)
    names = []
    for index in range(count):
        record = block[start + index * 16 : start + index * 16 + 16]
        kind = record[:8].rstrip(b"\0")
        name = record[8:].rstrip(b"\0").decode("latin1")
        if kind == b"i":
            names.append(name[: -len(".i")])
    return names


def picture_sources(data: bytes) -> Dict[int, bytes]:
    """Return {picture id: imported PNG bytes}."""
    entries = live_entries(data)
    sources = {}
    for picture_id, name in enumerate(picture_names(data)):
        entry = entries.get(name + ".is")
        if entry is None:
            continue
        block = data[entry.off : entry.off + entry.size]
        if block[:4] == PICTURE_SOURCE_MAGIC:
            sources[picture_id] = bytes(block[read_u32(block, 8) :])
    return sources


def build_picture_blocks(png: bytes):
    """Return (.is block, .i block) for a PNG; the .i data is stored uncompressed (mode 0)."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required to write pictures (pip install pillow)") from exc
    import io

    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    size = struct.pack("<HH", width, height)
    source = PICTURE_SOURCE_MAGIC + struct.pack("<II", 0, PICTURE_SOURCE_HEADER) + size
    source += struct.pack("<II", len(png), 0) + b"png" + png

    rgb = image.tobytes()
    pixels = bytearray()
    for i in range(0, len(rgb), 3):
        r, g, b = rgb[i], rgb[i + 1], rgb[i + 2]
        pixels += struct.pack("<H", ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    payload = bytes(PICTURE_RAW_DATA_HEADER) + bytes(pixels)
    picture = PICTURE_MAGIC + struct.pack("<II", 0, PICTURE_HEADER) + size + struct.pack("<II", len(payload), 0) + payload
    return source, picture


def relocate_block(data: bytearray, name: str, block: bytes) -> None:
    """Append `block` as the new content of resource `name`, marking the old entry stale (as Nextion Editor does)."""
    count = read_u32(data, 0)
    first_block = min(entry.off for entry in read_directory(data) if entry.size)
    if 4 + (count + 1) * DIRECTORY_ENTRY > first_block:
        raise RuntimeError(f"no room left in the .HMI directory for {name}")
    for index in range(count):
        off = 4 + index * DIRECTORY_ENTRY
        entry_name = data[off : off + 16].split(b"\0", 1)[0].decode("latin1")
        flags = read_u32(data, off + 24)
        if entry_name == name and not flags & 1:
            data[off : off + 16] = bytes(16)
            struct.pack_into("<I", data, off + 24, flags | 1)
            new_off = 4 + count * DIRECTORY_ENTRY
            data[new_off : new_off + 16] = name.encode("latin1").ljust(16, b"\0")
            struct.pack_into("<III", data, new_off + 16, len(data), len(block), flags & ~1)
            struct.pack_into("<I", data, 0, count + 1)
            data.extend(block)
            return
    raise RuntimeError(f"resource {name} not found in the .HMI directory")


def sync_pictures(variant: str, data: bytearray, details: List[str], check_only: bool) -> int:
    picture_dir = VARIANTS[variant][1].parent / f"{variant}_pictures"
    if not picture_dir.is_dir():
        return 0
    names = picture_names(data)
    sources = picture_sources(data)
    changed = 0
    for path in sorted(picture_dir.glob("*.png")):
        picture_id = int(path.stem)
        if picture_id >= len(names):
            raise RuntimeError(f"{path.name}: picture {picture_id} does not exist in {variant}")
        png = path.read_bytes()
        if sources.get(picture_id) == png:
            continue
        if not check_only:
            source, picture = build_picture_blocks(png)
            relocate_block(data, names[picture_id] + ".is", source)
            relocate_block(data, names[picture_id] + ".i", picture)
        details.append(f"  picture {picture_id}: {path.relative_to(REPO_ROOT)}")
        changed += 1
    return changed


def sync_variant(variant: str, check_only: bool, verbose: bool = False) -> str:
    hmi_path, code_dir = VARIANTS[variant]
    source_pages = parse_source_layouts(code_dir)
    source_code = parse_source_code(code_dir)
    data = bytearray(hmi_path.read_bytes())
    changed_pages = 0
    changed_fields = 0
    changed_events = 0
    details: List[str] = []

    for entry in read_directory(data):
        if entry.stale or not entry.name.endswith(".pa"):
            continue
        if entry.off + entry.size > len(data):
            raise RuntimeError(f"{hmi_path.name}:{entry.name} points outside the file")

        original = bytes(data[entry.off : entry.off + entry.size])
        if len(original) < 56 or read_u32(original, 4) != entry.size or read_u32(original, 8) != 56:
            continue

        page_name, header, objects = parse_page_block(original)
        if page_name not in source_pages and page_name not in source_code:
            continue

        page_field_changes = apply_layout_updates(page_name, objects, source_pages.get(page_name, {}))
        try:
            page_code_changes = apply_code_updates(page_name, objects, source_code.get(page_name, {}))
        except RuntimeError as exc:
            raise RuntimeError(f"{hmi_path.name}:{exc}") from exc
        if page_field_changes == 0 and not page_code_changes:
            continue
        details.extend(f"  {page_name}: code {event}" for event in page_code_changes)
        if page_field_changes:
            details.append(f"  {page_name}: {page_field_changes} layout field(s)")

        try:
            balance_length(page_name, header, objects, entry.size)
        except RuntimeError as exc:
            raise RuntimeError(f"{hmi_path.name}:{exc}") from exc
        rebuilt = build_page_block(page_name, header, objects)
        if len(rebuilt) != entry.size:
            raise RuntimeError(f"{hmi_path.name}:{page_name} changed size {entry.size}->{len(rebuilt)}")
        reseal(rebuilt, original)
        data[entry.off : entry.off + entry.size] = rebuilt
        changed_pages += 1
        changed_fields += page_field_changes
        changed_events += len(page_code_changes)

    try:
        changed_pictures = sync_pictures(variant, data, details, check_only)
    except RuntimeError as exc:
        raise RuntimeError(f"{hmi_path.name}:{exc}") from exc

    if not check_only and (changed_pages or changed_pictures):
        hmi_path.write_bytes(data)

    mode = "check" if check_only else "sync"
    if changed_pages or changed_pictures:
        summary = (f"{mode}: {variant}: {changed_pages} page(s), {changed_fields} field(s), "
                   f"{changed_events} event(s), {changed_pictures} picture(s)")
        return "\n".join([summary] + details) if verbose else summary
    return f"{mode}: {variant}: up to date"


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=sorted(VARIANTS), action="append", help="Variant(s) to process")
    parser.add_argument("--all", action="store_true", help="Process all known variants")
    parser.add_argument("--check", action="store_true", help="Report required updates without writing files")
    parser.add_argument("--list-variants", action="store_true", help="List the known HMI/code-dir mappings")
    parser.add_argument("-v", "--verbose", action="store_true", help="List each changed page, event and layout field")
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
            print(sync_variant(variant, check_only=args.check, verbose=args.verbose))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
