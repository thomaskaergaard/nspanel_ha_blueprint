#!/usr/bin/env python3
"""Compile a .HMI into a .tft by driving Nextion Editor's GUI (Windows only).

Nextion Editor has no command line compiler, so this script starts the editor
with the project, clicks Compile and copies the produced .tft out of the
editor's "bianyi" output folder. Every step leaves a screenshot in --debug-dir
so a failing CI run can be diagnosed from its artifacts.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pywinauto import Desktop, keyboard
from pywinauto.application import Application

DIALOG_BUTTONS = ("Yes", "OK", "Ok", "Confirm", "Continue", "是", "确定")
COMPILE_TITLES = ("Compile", "编译")


def screenshot(debug_dir: Path, name: str) -> None:
    try:
        from PIL import ImageGrab

        ImageGrab.grab().save(debug_dir / f"{time.strftime('%H%M%S')}_{name}.png")
    except Exception as err:  # screenshots are best effort
        print(f"screenshot {name} failed: {err}")


def dump_tree(window, debug_dir: Path, name: str) -> None:
    try:
        path = debug_dir / f"{name}_controls.txt"
        window.print_control_identifiers(depth=None, filename=str(path))
    except Exception as err:
        print(f"control dump {name} failed: {err}")


def bianyi_dirs(editor_dir: Path) -> list[Path]:
    dirs = [editor_dir / "bianyi"]
    for env in ("APPDATA", "LOCALAPPDATA"):
        if os.environ.get(env):
            dirs.append(Path(os.environ[env]) / "Nextion Editor" / "bianyi")
    return dirs


def search_roots(editor_dir: Path, hmi: Path) -> list[Path]:
    roots = [editor_dir.resolve(), hmi.parent]
    for env in ("USERPROFILE", "TEMP", "TMP", "PUBLIC", "ProgramData"):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]))
    return roots


def find_new_tft(roots: list[Path], since: float) -> Path | None:
    """The output folder differs between editor versions, so look everywhere likely."""
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda err: None):
            for name in filenames:
                if name.lower().endswith(".tft"):
                    f = Path(dirpath) / name
                    try:
                        if f.stat().st_mtime >= since and f.stat().st_size > 0:
                            return f
                    except OSError:
                        pass
    return None


def dismiss_dialogs(pid: int, main_handle: int | None, debug_dir: Path) -> bool:
    """Answer any popup owned by the editor. Returns True if one was handled."""
    handled = False
    for win in Desktop(backend="uia").windows(process=pid):
        if main_handle and win.handle == main_handle:
            continue
        title = win.window_text()
        print(f"dialog: {title!r}")
        screenshot(debug_dir, f"dialog_{title[:20] or 'untitled'}")
        dump_tree(Application(backend="uia").connect(handle=win.handle).window(handle=win.handle), debug_dir, f"dialog_{win.handle}")
        for label in DIALOG_BUTTONS:
            buttons = win.descendants(title=label, control_type="Button")
            if buttons:
                buttons[0].click_input()
                handled = True
                break
        else:
            win.set_focus()
            keyboard.send_keys("{ENTER}")
            handled = True
        time.sleep(2)
    return handled


def click_compile(main) -> bool:
    for title in COMPILE_TITLES:
        for ctrl in main.descendants(title=title):
            print(f"clicking {ctrl.element_info.control_type} {title!r}")
            ctrl.click_input()
            return True
    return False


def process_windows(pid: int):
    return Desktop(backend="uia").windows(process=pid)


def export_tft(app, main, pid: int, out_dir: Path, debug_dir: Path) -> None:
    before = {w.handle for w in process_windows(pid)}
    main.set_focus()
    main.child_window(title="File", control_type="MenuItem").click_input()
    time.sleep(2)
    screenshot(debug_dir, "file_menu")
    items = []
    for win in process_windows(pid):
        items += [c for c in win.descendants(control_type="MenuItem") if "tft" in c.window_text().lower()]
    print(f"TFT menu items: {[i.window_text() for i in items]}")
    if not items:
        for win in process_windows(pid):
            dump_tree(app.window(handle=win.handle), debug_dir, f"menu_{win.handle}")
        raise SystemExit("No TFT output entry in the File menu")
    items[0].click_input()
    time.sleep(3)

    dialogs = [w for w in process_windows(pid) if w.handle not in before]
    if not dialogs:
        dialogs = [w for w in process_windows(pid) if w.handle != main.handle]
    screenshot(debug_dir, "tft_output_dialog")
    for win in dialogs:
        print(f"output dialog: {win.window_text()!r}")
        dlg = app.window(handle=win.handle)
        dump_tree(dlg, debug_dir, f"output_dialog_{win.handle}")
        edits = dlg.descendants(control_type="Edit")
        print(f"edit fields: {[e.window_text() for e in edits]}")
        if edits:
            edits[0].set_edit_text(str(out_dir))
        buttons = dlg.descendants(control_type="Button")
        print(f"buttons: {[b.window_text() for b in buttons]}")
        for b in buttons:
            if b.window_text().strip().lower() in ("output", "ok", "export", "save"):
                b.click_input()
                print(f"clicked {b.window_text()!r}")
                break
    time.sleep(3)
    screenshot(debug_dir, "tft_output_clicked")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hmi", type=Path)
    parser.add_argument("--editor-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--debug-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--compile-wait", type=int, default=90)
    args = parser.parse_args()

    hmi = args.hmi.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    debug_dir = args.debug_dir / hmi.stem
    debug_dir.mkdir(parents=True, exist_ok=True)
    for d in bianyi_dirs(args.editor_dir):
        shutil.rmtree(d, ignore_errors=True)

    exe = args.editor_dir.resolve() / "Nextion Editor.exe"
    started = time.time()
    proc = subprocess.Popen([str(exe), str(hmi)], cwd=str(exe.parent))
    app = Application(backend="uia").connect(process=proc.pid, timeout=120)
    main = app.top_window()
    main.wait("visible", timeout=180)
    print(f"editor window: {main.window_text()!r}")
    screenshot(debug_dir, "started")

    # Give the project time to load, answering upgrade/info prompts meanwhile.
    deadline = time.time() + 180
    while time.time() < deadline:
        main = app.top_window()
        if hmi.stem.lower() in main.window_text().lower():
            break
        if not dismiss_dialogs(proc.pid, None, debug_dir):
            time.sleep(3)
    main = app.window(handle=main.handle)
    print(f"project window: {main.window_text()!r}")
    time.sleep(10)
    screenshot(debug_dir, "loaded")
    dump_tree(main, debug_dir, "main")

    if not click_compile(main):
        print("Compile control not found, pressing F5")
        main.set_focus()
        keyboard.send_keys("{F5}")
    compile_started = time.time()
    time.sleep(args.compile_wait)
    screenshot(debug_dir, "compiled")

    # Compile only checks the project; File > "TFT file output" writes the .tft.
    export_tft(app, main, proc.pid, args.out_dir.resolve(), debug_dir)

    roots = [args.out_dir.resolve()] + search_roots(args.editor_dir, hmi)
    tft = None
    while time.time() - started < args.timeout:
        tft = find_new_tft(roots, compile_started - 5)
        if tft:
            print(f"found {tft}")
            # Wait until the editor has finished writing it.
            size = -1
            while size != tft.stat().st_size:
                size = tft.stat().st_size
                time.sleep(5)
            break
        dismiss_dialogs(proc.pid, main.handle, debug_dir)
        time.sleep(5)
    screenshot(debug_dir, "finished")
    output = [c.window_text() for c in main.descendants() if "Compile" in c.window_text() and "Success" in c.window_text()]
    print(f"compile output: {output}")

    proc.kill()
    if not tft:
        print("No .tft produced before timeout", file=sys.stderr)
        return 1
    target = args.out_dir / f"{hmi.stem}.tft"
    if tft.resolve() != target.resolve():
        shutil.copy2(tft, target)
    print(f"{target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
