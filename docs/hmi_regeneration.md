# Regenerating `.HMI` Files

This repository now includes a helper for keeping the shipped `.HMI` files in sync with the tracked Nextion page text exports under `hmi/dev/*_code/`.

## What the tool does

The tool updates the existing `.HMI` variants in place by:

- reading the current `.HMI` container
- finding the live page blocks already present in that file
- copying supported layout values and event code from the tracked text exports
- keeping each page block at its original size
- recalculating the page checksum

It is designed for the workflow used in this repository, where the `.HMI` files already contain the required pages and components.

## Location

Tool:

- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/tools/nextion_hmi_sync.py`

Tracked page text sources:

- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/hmi/dev/nspanel_eu_code/`
- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/hmi/dev/nspanel_us_code/`
- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/hmi/dev/nspanel_us_land_code/`
- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/hmi/dev/nspanel_CJK_eu_code/`
- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/hmi/dev/nspanel_CJK_us_code/`
- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/hmi/dev/nspanel_CJK_us_land_code/`

## Supported workflow

1. Edit the tracked page text files in `hmi/dev/*_code/`.
2. Run the sync tool to update the matching `.HMI` file(s).
3. Open the updated `.HMI` in Nextion Editor.
4. Compile a new `.tft`.

## Commands

List the supported variants:

```bash
python tools/nextion_hmi_sync.py --list-variants
```

Check whether a variant needs to be rebuilt (add `-v` to list each changed page, event and field):

```bash
python tools/nextion_hmi_sync.py --check --variant nspanel_eu
python tools/nextion_hmi_sync.py --check --all -v
```

Sync one variant:

```bash
python tools/nextion_hmi_sync.py --variant nspanel_eu
```

Sync all known variants:

```bash
python tools/nextion_hmi_sync.py --all
```

## What is currently synchronized

The tool currently synchronizes these existing page object fields from the tracked text exports:

- `x coordinate`
- `y coordinate`
- `Width`
- `Height`
- `Max. Text Size`

When the target object exposes `endx` and `endy` in the page block, those are recomputed automatically from the new position and size.

It also synchronizes the **event code** of existing objects (Preinitialize, Postinitialize, Touch Press, Touch Release, Touch Move, Timer and Page Exit events). Edit the code in the text export, run the tool, and the `.HMI` compiles without manual edits in Nextion Editor. How the text exports map onto the `.HMI`:

- the exports indent 4 spaces per level, while the `.HMI` stores 2
- raw bytes are written as `\xNN` in the exports (for example `\xe2\x97\x8f` for `●`)
- Nextion escapes such as `\r` are kept literally, exactly as typed in Nextion Editor
- blank lines and trailing whitespace are ignored

## Pictures

PNG files in `hmi/dev/<variant>_pictures/<picture id>.png` replace the picture with that id. For each changed picture, the tool writes the PNG (`.is`) and an uncompressed RGB565 copy (`.i`, the data compiled into the TFT). It appends both at the end of the `.HMI` and marks the old entries stale, the same way Nextion Editor does. The file grows by about 300 KB for each 480×320 picture you change. Pillow is required for this step (`pip install pillow`).

### Button page backgrounds

Buttons on the button pages are drawn by cropping picture 46 (off) or 47 (on) behind each button component, so those pictures must have a rounded rectangle exactly where each `buttonNNpic` sits. After changing the button layout, regenerate them from the text exports, then sync:

```bash
python tools/render_button_backgrounds.py --all
python tools/nextion_hmi_sync.py --all
```

The render script draws the rectangles on top of the page background (picture 0), using the style of the original pictures: 85% opaque, 3 px narrower and 4 px shorter than the component, 16 px corner radius. All eight button pages must share one layout.

## Page block size

The checksum of a page block can only be recomputed for block sizes that already exist, so the tool always writes a page back at its original size:

- if the code got shorter, a comment line `//hmi_sync pad---…` is added to the page's Page Exit event (comments are not compiled into the TFT)
- if the code got longer, the pad line shrinks first, then leading indentation is trimmed from code lines, starting with the last event of the page (Nextion ignores indentation)

Pad lines are never written to the text exports, and indentation differences are ignored when comparing. A page without any indentation left (for example `debug`) can only take changes that don't make it longer. Make such a change size-neutral (for example, shorten a label by the bytes you add) or the tool stops with an error for that page.

## Important limitations

This tool does **not** rebuild the entire Nextion container from scratch. In practice that means:

- it can update existing components, their event code and existing pictures
- it cannot add or remove pages, objects, events, fonts, or pictures
- it cannot rewrite the `.HMI` directory structure or `Program.s`
- it stops with an error if a page cannot be kept at its original size

If you need a structural change such as:

- adding new resources
- changing object counts
- changing the packed page size
- rebuilding from a completely different base `.HMI`

then you still need a Nextion-Editor-based workflow for that step.

## Typical repository workflow

Example after editing the EU 320px button pages:

```bash
python tools/nextion_hmi_sync.py --variant nspanel_eu
python tools/nextion_hmi_sync.py --variant nspanel_CJK_eu
```

Then open these updated files in Nextion Editor and compile:

- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/hmi/nspanel_eu.HMI`
- `/home/runner/work/nspanel_ha_blueprint/nspanel_ha_blueprint/hmi/nspanel_CJK_eu.HMI`
