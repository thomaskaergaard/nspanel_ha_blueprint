#!/usr/bin/env python3
"""Make structural .HMI changes by driving Nextion Editor's GUI (Windows only).

The .HMI container keeps a checksummed copy of its resource directory that only
Nextion Editor can write, so adding pages or pictures has to go through the
editor. This script opens a project, applies the requested steps and saves it:

  --copy-page NAME   copy page NAME and move the copy to the end of the page list
  --add-picture PNG  import a picture; it gets the next free picture id
  --replace-picture ID  replace picture ID with hmi/dev/ui/<model>/pics/<ID>.png

Every step leaves a screenshot in --debug-dir. Coordinates assume the 1024x768
desktop of the GitHub Windows runner and the editor's default window layout.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from pywinauto import Desktop, keyboard, mouse
from pywinauto.application import Application

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nextion_compile_tft as compile_tft  # noqa: E402
import nextion_hmi_sync as hmi  # noqa: E402

PAGE_LIST_FIRST_ROW = (900, 178)  # row "0 boot" in the Page panel
PAGE_COPY_BUTTON = (970, 157)
PAGE_DOWN_BUTTON = (941, 157)
PICTURE_ADD_BUTTON = (18, 441)
PICTURE_REPLACE_BUTTON = (75, 441)
PICTURE_LIST_FIRST = (95, 520)  # first thumbnail; Down moves the selection one picture


def page_names(hmi_path: Path) -> list[str]:
    data = hmi_path.read_bytes()
    live = hmi.live_entries(data)
    main = live["main.HMI"]
    block = data[main.off : main.off + main.size]
    start, count = hmi.read_u32(block, 24), hmi.read_u32(block, 28)
    names = []
    for index in range(count):
        record = block[start + index * 16 : start + index * 16 + 16]
        if record[:8].rstrip(b"\0") == b"pa":
            entry = live[record[8:].rstrip(b"\0").decode("latin1")]
            names.append(hmi.parse_page_block(data[entry.off : entry.off + entry.size])[0])
    return names


def shot(debug_dir: Path, name: str) -> None:
    compile_tft.screenshot(debug_dir, name)


def copy_page(main, name: str, pages: list[str], debug_dir: Path) -> None:
    main.set_focus()
    mouse.click(coords=PAGE_LIST_FIRST_ROW)
    time.sleep(1)
    for _ in range(pages.index(name)):
        keyboard.send_keys("{DOWN}")
        time.sleep(0.2)
    time.sleep(1)
    shot(debug_dir, f"selected_{name}")
    mouse.click(coords=PAGE_COPY_BUTTON)
    time.sleep(3)
    shot(debug_dir, "copied")
    # Move the copy to the end so the existing page ids stay the same.
    for _ in range(len(pages)):
        mouse.click(coords=PAGE_DOWN_BUTTON)
        time.sleep(0.5)
    shot(debug_dir, "copy_moved_to_end")


def wait_file_dialog(pid: int, timeout: float = 20):
    """The common Open dialog of the editor process (window class #32770)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for win in Desktop(backend="win32").windows(process=pid, class_name="#32770", visible_only=True):
            return win
        time.sleep(0.5)
    return None


def list_windows(pid: int) -> None:
    for backend in ("win32", "uia"):
        for win in Desktop(backend=backend).windows(process=pid):
            try:
                print(f"  [{backend}] {win.window_text()!r} class={win.class_name()} visible={win.is_visible()}")
            except Exception:
                pass


def click_picture_button(main, coords) -> bool:
    """Click a Picture panel toolbar button through UI Automation (plain clicks only show the tooltip)."""
    x, y = coords
    for button in main.descendants(control_type="Button"):
        rect = button.rectangle()
        if rect.left <= x <= rect.right and rect.top <= y <= rect.bottom:
            print(f"Picture add button: {button.window_text()!r} {rect}")
            try:
                button.invoke()
            except Exception:
                button.click_input()
            return True
    return False


def add_picture(app, main, pid: int, png: Path, debug_dir: Path) -> None:
    main.set_focus()
    if not click_picture_button(main, PICTURE_ADD_BUTTON):
        print("Picture add button not found via UIA; pressing it with the mouse")
        mouse.press(coords=PICTURE_ADD_BUTTON)
        time.sleep(0.2)
        mouse.release(coords=PICTURE_ADD_BUTTON)
    dialog = wait_file_dialog(pid)
    shot(debug_dir, f"picture_dialog_{png.stem}")
    if dialog is None:
        print("windows after clicking add picture:")
        list_windows(pid)
        raise SystemExit("no file dialog after clicking add picture")
    print(f"file dialog: {dialog.window_text()!r}")
    dialog.set_focus()
    time.sleep(1)
    keyboard.send_keys(str(png.resolve()), with_spaces=True)  # the file name box has the focus
    time.sleep(1)
    keyboard.send_keys("{ENTER}")
    time.sleep(6)
    shot(debug_dir, f"picture_added_{png.stem}")
    # Confirm "Import successfully 1 pieces".
    keyboard.send_keys("{ENTER}")
    time.sleep(3)
    shot(debug_dir, f"picture_confirmed_{png.stem}")


def find_picture_file(hmi_path: Path, picture_id: int) -> Path:
    folder = hmi.picture_dir(hmi_path.stem)
    for suffix in (".png", ".jpg"):
        if (folder / f"{picture_id}{suffix}").exists():
            return folder / f"{picture_id}{suffix}"
    raise SystemExit(f"no {folder}/{picture_id}.png")


def replace_picture(main, pid: int, picture_id: int, image: Path, debug_dir: Path, picture_count: int) -> None:
    main.set_focus()
    mouse.click(coords=PICTURE_LIST_FIRST)  # any thumbnail: the list may be scrolled
    time.sleep(1)
    for _ in range(picture_count + 5):
        keyboard.send_keys("{UP}")  # Up stops at picture 0
        time.sleep(0.05)
    for _ in range(picture_id):
        keyboard.send_keys("{DOWN}")
        time.sleep(0.1)
    time.sleep(1)
    shot(debug_dir, f"selected_picture_{picture_id}")
    if not click_picture_button(main, PICTURE_REPLACE_BUTTON):
        raise SystemExit("Picture replace button not found")
    dialog = wait_file_dialog(pid)
    shot(debug_dir, f"replace_dialog_{picture_id}")
    if dialog is None:
        list_windows(pid)
        raise SystemExit(f"no file dialog after clicking replace for picture {picture_id}")
    dialog.set_focus()
    time.sleep(1)
    keyboard.send_keys(str(image.resolve()), with_spaces=True)
    time.sleep(1)
    keyboard.send_keys("{ENTER}")
    time.sleep(6)
    shot(debug_dir, f"picture_replaced_{picture_id}")
    keyboard.send_keys("{ENTER}")  # confirm the import message
    time.sleep(3)
    shot(debug_dir, f"picture_replace_confirmed_{picture_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hmi", type=Path)
    parser.add_argument("--editor-dir", type=Path, required=True)
    parser.add_argument("--debug-dir", type=Path, required=True)
    parser.add_argument("--copy-page")
    parser.add_argument("--add-picture", type=Path, action="append", default=[])
    parser.add_argument("--replace-picture", type=int, action="append", default=[],
                        help="picture id to replace with hmi/dev/ui/<model>/pics/<id>.png")
    args = parser.parse_args()

    hmi_path = args.hmi.resolve()
    debug_dir = args.debug_dir / f"{hmi_path.stem}_structure"
    debug_dir.mkdir(parents=True, exist_ok=True)
    pages = page_names(hmi_path)
    # Count before the editor opens the file (Windows then blocks reading it).
    picture_count = len(hmi.picture_names(hmi_path.read_bytes())) + len(args.add_picture)
    print(f"{len(pages)} pages: {', '.join(pages)}")

    exe = args.editor_dir.resolve() / "Nextion Editor.exe"
    proc = subprocess.Popen([str(exe), str(hmi_path)], cwd=str(exe.parent))
    app = Application(backend="uia").connect(process=proc.pid, timeout=120)
    main = None
    deadline = time.time() + 300
    while time.time() < deadline and main is None:
        for win in compile_tft.process_windows(proc.pid):
            if hmi_path.stem.lower() in win.window_text().lower():
                main = app.window(handle=win.handle)
        time.sleep(3)
    if main is None:
        shot(debug_dir, "no_project_window")
        raise SystemExit("Project window did not appear")
    time.sleep(10)
    shot(debug_dir, "loaded")

    if args.copy_page:
        copy_page(main, args.copy_page, pages, debug_dir)
    for png in args.add_picture:
        add_picture(app, main, proc.pid, png, debug_dir)
    for picture_id in args.replace_picture:
        replace_picture(main, proc.pid, picture_id, find_picture_file(hmi_path, picture_id), debug_dir, picture_count)

    main.set_focus()
    saved = False
    for button in main.descendants(title="Save", control_type="Button"):
        print(f"Save button: {button.rectangle()}")
        try:
            button.invoke()
        except Exception:
            button.click_input()
        saved = True
        break
    if not saved:
        print("Save button not found; pressing Ctrl+S")
        keyboard.send_keys("^s")
    time.sleep(20)
    shot(debug_dir, "saved")
    compile_tft.dismiss_dialogs(proc.pid, main.handle, debug_dir)
    proc.kill()
    time.sleep(3)

    after = page_names(hmi_path)
    print(f"after save: {len(after)} pages, last: {after[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
