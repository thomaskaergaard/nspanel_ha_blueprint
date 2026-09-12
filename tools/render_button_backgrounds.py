#!/usr/bin/env python3
"""Render the button-page background pictures from the tracked button layout.

ESPHome shows a button by cropping picture 46 (off) or 47 (on) behind the button
components, so those pictures must contain a rounded rectangle exactly where each
``buttonNNpic`` component sits. This script redraws both pictures on top of the
page background (picture 0) using the layout in ``hmi/dev/<variant>_code/buttonpage01.txt``
and writes them to ``hmi/dev/<variant>_pictures/<picture id>.png``.
Run ``nextion_hmi_sync.py`` afterwards to store them in the .HMI files.

Requires Pillow.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image, ImageChops, ImageDraw

from nextion_hmi_sync import REPO_ROOT, VARIANTS, parse_source_layouts, picture_sources

BUTTON_PAGES = [f"buttonpage{index:02d}" for index in range(1, 9)]
BUTTON_COUNT = 10
BACKGROUND_PICTURE = 0
BUTTON_PICTURES = {46: (44, 44, 44), 47: (255, 255, 255)}  # picture id -> fill colour (off / on)

# Style measured from the original 4x2 pictures: 85% opaque rounded rectangle,
# 3 px narrower and 4 px shorter than the button component, 16 px corner radius.
OPACITY = 0.851
RADIUS = 16
INSET_W = 3
INSET_H = 4
SUPERSAMPLE = 4


def pictures_dir(variant: str) -> Path:
    return REPO_ROOT / "hmi" / "dev" / f"{variant}_pictures"


def button_boxes(variant: str) -> List[Tuple[int, int, int, int]]:
    layouts = parse_source_layouts(VARIANTS[variant][1])
    boxes = None
    for page in BUTTON_PAGES:
        page_boxes = []
        for index in range(1, BUTTON_COUNT + 1):
            obj = layouts[page][f"button{index:02d}pic"]
            page_boxes.append((obj["x"], obj["y"], obj["w"], obj["h"]))
        if boxes is None:
            boxes = page_boxes
        elif page_boxes != boxes:
            raise RuntimeError(f"{variant}: {page} button layout differs from {BUTTON_PAGES[0]}; "
                               "one background picture cannot serve both")
    return boxes


def render(background: Image.Image, boxes: Iterable[Tuple[int, int, int, int]], colour) -> Image.Image:
    width, height = background.size
    mask = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 0)
    draw = ImageDraw.Draw(mask)
    for x, y, w, h in boxes:
        draw.rounded_rectangle(
            [x * SUPERSAMPLE, y * SUPERSAMPLE, (x + w - INSET_W) * SUPERSAMPLE - 1, (y + h - INSET_H) * SUPERSAMPLE - 1],
            radius=RADIUS * SUPERSAMPLE,
            fill=round(255 * OPACITY),
        )
    mask = mask.resize((width, height), Image.BOX)
    return Image.composite(Image.new("RGB", (width, height), colour), background, mask)


def render_variant(variant: str, check_only: bool) -> str:
    sources = picture_sources(VARIANTS[variant][0].read_bytes())
    background = Image.open(io.BytesIO(sources[BACKGROUND_PICTURE])).convert("RGB")
    boxes = button_boxes(variant)
    out_dir = pictures_dir(variant)
    changed = []
    for picture_id, colour in BUTTON_PICTURES.items():
        image = render(background, boxes, colour)
        path = out_dir / f"{picture_id}.png"
        if path.exists():
            current = Image.open(path).convert("RGB")
            if current.size == image.size and ImageChops.difference(current, image).getbbox() is None:
                continue
        changed.append(path.name)
        if not check_only:
            out_dir.mkdir(parents=True, exist_ok=True)
            image.save(path, optimize=True)
    mode = "check" if check_only else "render"
    return f"{mode}: {variant}: {', '.join(changed) if changed else 'up to date'}"


def main(argv: Iterable[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", choices=sorted(VARIANTS), action="append", help="Variant(s) to render")
    parser.add_argument("--all", action="store_true", help="Render all known variants")
    parser.add_argument("--check", action="store_true", help="Report pictures that would change without writing")
    args = parser.parse_args(list(argv))
    if not args.all and not args.variant:
        parser.error("choose --variant or --all")
    try:
        for variant in sorted(VARIANTS) if args.all else args.variant:
            print(render_variant(variant, args.check))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
