#!/usr/bin/env python3
"""Rebuild the `alarm` page of the EU .HMI files as a floor plan of tap areas.

The page keeps its name (so navigation and page ids stay valid) but its alarm
components are replaced by one crop-image Text per area in
hmi/dev/floorplan_eu.json. Picture 44 is the page background (lights off) and
picture 45 the same plan with every area lit; an area shows as lit when its
component's `picc` is 45. The components are global, so Home Assistant can set
them with `alarm.<area>.picc=45` while another page is shown.

Touching an area sends `floorplan,toggle,<area>` and opening the page sends
`floorplan,opened`; ESPHome forwards both to Home Assistant as
`esphome.nspanel_ha_blueprint` events.

The page keeps its original object count (spare variables fill the gap) and its
block is padded back to its original length (see nextion_hmi_sync.py),
and hmi/dev/<variant>_code/alarm.txt is rewritten to match, so the sync tool
leaves the page alone afterwards.
"""

from __future__ import annotations

import copy
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nextion_hmi_sync as hmi  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LAYOUT = REPO / "hmi" / "dev" / "floorplan_eu.json"
VARIANTS = ("nspanel_eu", "nspanel_CJK_eu")  # both 480x320 and share hmi/dev/ui/eu/pics
PAGE = "alarm"
TEMPLATE_PAGE, TEMPLATE_OBJECT = "buttonpage01", "button01pic"
TITLE = "Lys"


def send_event(payload_expr: str) -> list[str]:
    return [
        f"lastclick.txt={payload_expr}",
        "printh 92",
        'prints "localevent",0',
        "printh 00",
        "prints lastclick.txt,0",
        "printh 00",
        "printh FF FF FF",
    ]


SEND_TIMER = "send_tm"


def queue_event(payload: str) -> list[str]:
    """Store the payload and let the send timer transmit it (keeps each component's code short)."""
    return [f'lastclick.txt="{payload}"', f"{SEND_TIMER}.en=1"]


def page_load(title: bool) -> list[str]:
    lines = ["dim=brightness", "if(api==0)", "{", "    page home_page_id", "}"]
    if title:
        lines.append(f'page_label.txt="{TITLE}"')
    return lines + queue_event("floorplan,opened") + ["sendme"]


def attrs(items: list) -> dict:
    return {item["key"]: item for item in items if item["kind"] == "attr"}


def set_attr(items: list, key: str, value) -> None:
    item = attrs(items)[key]
    if isinstance(value, str):
        item["raw"] = value.encode("latin1")
    else:
        item["raw"] = hmi.encode_value(value, len(item["raw"]))
    item["value"] = value


def set_geometry(items: list, x: int, y: int, w: int, h: int) -> None:
    for key, value in (("x", x), ("y", y), ("w", w), ("h", h), ("endx", x + w - 1), ("endy", y + h - 1)):
        set_attr(items, key, value)


def set_events(items: list, events: dict) -> None:
    """Replace the code of the given events; `events` maps event mark -> source lines."""
    # Back to front, so replacing one event's code doesn't move the marks still to do.
    for mark_index, event, count in reversed(list(hmi.split_events(items))):
        if event in events:
            lines = [hmi.encode_code_line(line) for line in events[event]]
            hmi.set_event_lines(items, mark_index, event, count, lines)


def read_pages(data: bytes) -> dict:
    pages = {}
    for entry in hmi.read_directory(data):
        if entry.stale or not entry.name.endswith(".pa"):
            continue
        block = bytes(data[entry.off : entry.off + entry.size])
        name, header, objects = hmi.parse_page_block(block)
        pages[name] = (entry, block, header, objects)
    return pages


def find_object(pages: dict, page: str, name: str) -> list:
    return next(items for items in pages[page][3] if hmi.object_name(items, page) == name)


def build_objects(alarm_objects: list, templates: dict, areas: list, title: bool = True) -> list:
    by_name = {hmi.object_name(items, PAGE): items for items in alarm_objects}
    template = templates["area"]

    page = copy.deepcopy(by_name[PAGE])
    set_attr(page, "sta", 2)  # background: picture
    set_attr(page, "pic", 44)
    set_events(page, {"codesload": page_load(title), "codesloadend": [], "codesdown": [], "codesup": [], "codesunload": []})

    timer = copy.deepcopy(templates["timer"])
    set_attr(timer, "objname", SEND_TIMER)
    set_attr(timer, "en", 0)
    set_attr(timer, "tim", 50)
    set_events(timer, {"codestimer": [f"{SEND_TIMER}.en=0", *send_event("lastclick.txt")[1:]]})

    back = copy.deepcopy(by_name["button_back"])
    set_geometry(back, 385, 0, 95, 40)

    lastclick = copy.deepcopy(by_name["lastclick"])
    set_attr(lastclick, "txt_maxl", 40)  # longest payload: floorplan,toggle,<area>
    objects = [page, lastclick, timer]
    if title:
        label = copy.deepcopy(by_name["page_label"])
        set_geometry(label, 368, 48, 108, 32)
        objects.append(label)
    longest = max(len(f"floorplan,toggle,{area['name']}") for area in areas)
    if longest > 40:
        raise SystemExit(f"area names too long for lastclick ({longest} > 40 characters)")
    for area in areas:
        items = copy.deepcopy(template)
        set_attr(items, "objname", area["name"])
        set_attr(items, "vscope", 1)  # global: settable from other pages
        set_attr(items, "picc", 44)
        set_geometry(items, area["x"], area["y"], area["w"], area["h"])
        set_events(items, {"codesdown": [], "codesup": queue_event(f"floorplan,toggle,{area['name']}")})
        objects.append(items)
    objects.append(back)

    # Nextion Editor rejects a page whose object count changed ("Version mismatch"),
    # so fill up to the original count with spare string variables.
    if len(objects) > len(alarm_objects):
        raise SystemExit(f"{len(objects)} objects don't fit the page's {len(alarm_objects)}")
    for number in range(1, len(alarm_objects) - len(objects) + 1):
        spare = copy.deepcopy(templates["spare"])
        set_attr(spare, "objname", f"spare{number}")
        set_attr(spare, "txt_maxl", 1)  # display RAM is reserved per string variable
        objects.append(spare)

    for index, items in enumerate(objects):
        set_attr(items, "id", index)
    return objects


def export_text(objects: list) -> str:
    """Text export in the format nextion_hmi_sync.py reads (layout + event code)."""
    kinds = {121: "Page", 116: "Text", 98: "Button", 52: "Variable (string)", 51: "Timer"}
    events = {mark: label for label, mark in hmi.SOURCE_EVENTS.items()}
    out = []
    for items in objects:
        a = attrs(items)
        name = hmi.object_name(items, PAGE)
        out.append(f"{kinds[a['type']['value']]} {name}")
        out.append("    Attributes")
        out.append(f"        ID                 : {a['id']['value']}")
        out.append(f"        Scope              : {'global' if a['vscope']['value'] else 'local'}")
        if "x" in a:
            for label, key in (("x coordinate", "x"), ("y coordinate", "y"), ("Width", "w"), ("Height", "h")):
                out.append(f"        {label:<19}: {a[key]['value']}")
        code = []
        for mark_index, event, count in hmi.split_events(items):
            lines = [
                item["raw"]
                for item in items[mark_index + 1 : mark_index + 1 + count]
                if item["kind"] == "code" and not hmi.is_pad(item["raw"])
            ]
            if lines and event in events:
                code.append(f"        {events[event]}")
                for raw in lines:
                    text = raw.decode("utf-8")
                    stripped = text.lstrip(" ")
                    code.append(" " * (12 + 2 * (len(text) - len(stripped))) + stripped)
                code.append("")
        if code:
            out.append("")
            out.append("    Events")
            out.extend(code)
        out.append("")
    return "\n".join(out)


def build_variant(variant: str, layout: dict) -> None:
    hmi_path = REPO / "hmi" / f"{variant}.HMI"
    export = REPO / "hmi" / "dev" / f"{variant}_code" / "alarm.txt"
    data = bytearray(hmi_path.read_bytes())
    pages = read_pages(data)
    entry, original, header, alarm_objects = pages[PAGE]
    templates = {
        "area": find_object(pages, TEMPLATE_PAGE, TEMPLATE_OBJECT),
        "timer": find_object(pages, TEMPLATE_PAGE, "click_timer"),
        "spare": find_object(pages, PAGE, "lastclick"),  # a plain variable, no code
    }
    for title in (True, False):
        objects = build_objects(alarm_objects, templates, layout["areas"], title)
        try:
            hmi.balance_length(PAGE, header, objects, entry.size)
            break
        except RuntimeError as exc:
            if not title:
                raise
            print(f"{variant}: without the title: {exc}")
    block = hmi.build_page_block(PAGE, header, objects)
    if len(block) != entry.size:
        raise SystemExit(f"{variant}: page size {len(block)} != {entry.size}")
    hmi.reseal(block, original)
    data[entry.off : entry.off + entry.size] = block
    hmi_path.write_bytes(data)
    export.write_text(export_text(objects), encoding="utf-8")
    print(f"{hmi_path.name}: {PAGE} rebuilt with {len(layout['areas'])} areas ({len(objects)} objects, {entry.size} bytes)")


def main() -> int:
    layout = json.loads(LAYOUT.read_text())
    if (layout["picture_off"], layout["picture_on"]) != (44, 45):
        raise SystemExit("this page expects pictures 44 (off) and 45 (on)")
    for variant in VARIANTS:
        build_variant(variant, layout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
