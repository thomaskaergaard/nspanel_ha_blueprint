# Regenerating `.HMI` Files

This repository now includes a helper for keeping the shipped `.HMI` files in sync with the tracked Nextion page text exports under `hmi/dev/*_code/`.

## What the tool does

The tool updates the existing `.HMI` variants in place by:

- reading the current `.HMI` container
- finding the live page blocks already present in that file
- copying supported layout values from the tracked text exports
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

Check whether a variant needs to be rebuilt:

```bash
python tools/nextion_hmi_sync.py --check --variant nspanel_eu
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

This is enough for the button-page layout work that was recently added to the repository and for future position/size tweaks on existing components.

## Important limitations

This tool does **not** rebuild the entire Nextion container from scratch.

It only supports length-preserving updates to page blocks that already exist in the `.HMI` file. In practice that means:

- it can update existing components already present in the page
- it can update page layout/geometry without changing the page block size
- it cannot add or remove pages, objects, fonts, or images
- it cannot rewrite the `.HMI` directory structure
- it will stop with an error if a change would alter a page block length

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
