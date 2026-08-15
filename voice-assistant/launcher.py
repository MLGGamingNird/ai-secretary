"""
VESPER launcher -- runs the brain and the real Electron overlay as hidden
background processes, controlled from a system tray icon. No terminal
windows needed, and no browser involved.

Setup (one-time):
    1. Open a terminal, go to the brain folder, activate its venv:
         cd brain
         venv\\Scripts\\activate
    2. Install the two extra packages this launcher needs:
         pip install pystray pillow
    3. Go back to the project root:
         cd ..
    4. Run it:
         brain\\venv\\Scripts\\python.exe launcher.py

To make it start automatically with Windows, see the README section on
auto-launch -- it's a one-time shortcut setup, not more code.
"""

import os
import subprocess
import sys
import time

import pystray
from PIL import Image, ImageDraw

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BRAIN_DIR = os.path.join(PROJECT_ROOT, "brain")
DESKTOP_DIR = os.path.join(PROJECT_ROOT, "desktop")
VENV_PYTHON = os.path.join(BRAIN_DIR, "venv", "Scripts", "python.exe")
ELECTRON_EXE = os.path.join(DESKTOP_DIR, "node_modules", ".bin", "electron.cmd")

# On Windows, this flag stops child processes from popping up their own
# console windows -- this is what makes the whole thing feel like a real
# background app instead of a pile of terminals.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

brain_process = None
electron_process = None


def start_brain():
    global brain_process
    if brain_process and brain_process.poll() is None:
        return  # already running
    log_file = open(os.path.join(PROJECT_ROOT, "brain_log.txt"), "a")
    brain_process = subprocess.Popen(
        [VENV_PYTHON, "-m", "uvicorn", "main:app", "--port", "8000"],
        cwd=BRAIN_DIR,
        creationflags=CREATE_NO_WINDOW,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def start_electron():
    global electron_process
    if electron_process and electron_process.poll() is None:
        return  # already running

    if not os.path.exists(ELECTRON_EXE):
        print(f"Couldn't find Electron at {ELECTRON_EXE}")
        print("Make sure you've run 'npm install' inside desktop/ first.")
        return

    log_file = open(os.path.join(PROJECT_ROOT, "electron_log.txt"), "a")
    # electron.cmd is a Windows batch script, not a real .exe -- it needs to
    # be run through cmd.exe to actually execute (running it directly can
    # silently fail to launch anything, with no error shown anywhere).
    electron_process = subprocess.Popen(
        ["cmd", "/c", ELECTRON_EXE, "."],
        cwd=DESKTOP_DIR,
        creationflags=CREATE_NO_WINDOW,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def stop_everything():
    for proc in (brain_process, electron_process):
        if proc and proc.poll() is None:
            proc.terminate()


def make_tray_icon_image():
    """Draws a simple filled circle as the tray icon, so we don't need to
    ship a separate .ico file."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(58, 123, 253, 255))
    return image


def on_reopen(icon, item):
    """Relaunches the Electron window if you've closed it -- the brain
    keeps running the whole time regardless, so this is quick."""
    start_electron()


def on_restart_brain(icon, item):
    global brain_process
    if brain_process and brain_process.poll() is None:
        brain_process.terminate()
        time.sleep(1)
    start_brain()


def on_quit(icon, item):
    stop_everything()
    icon.stop()


def main():
    if not os.path.exists(VENV_PYTHON):
        print(f"Couldn't find {VENV_PYTHON}")
        print("Make sure you've created the venv inside brain/ first (see README).")
        return

    start_brain()
    time.sleep(2)  # give the brain a moment to actually come up first
    start_electron()

    menu = pystray.Menu(
        pystray.MenuItem("Reopen VESPER window", on_reopen, default=True),
        pystray.MenuItem("Restart brain (e.g. after code changes)", on_restart_brain),
        pystray.MenuItem("Quit VESPER", on_quit),
    )
    icon = pystray.Icon("vesper", make_tray_icon_image(), "VESPER", menu)

    try:
        icon.run()
    finally:
        stop_everything()


if __name__ == "__main__":
    main()
