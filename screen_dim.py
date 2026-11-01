#!/usr/bin/env python3
"""
Screen Dim - black out the monitor but keep HDMI sound playing (Raspberry Pi 5).

The HDMI signal stays on, so the monitor's speakers keep working. The screen is
covered with a black window and, if the monitor supports DDC/CI, its brightness
is set to 0. The previous brightness is saved and put back when you restore.

Install from GitHub (once):
  curl -fsSL https://raw.githubusercontent.com/luis199421/pi-5-dimming/main/screen_dim.py -o /tmp/screen_dim.py && python3 /tmp/screen_dim.py --install

Install from a downloaded copy (once):
  python3 screen_dim.py --install            menu entry + shortcut Super+B
  python3 screen_dim.py --install C-A-s      same, with another key (Ctrl+Alt+S)

Use:
  screen_dim.py                 control window
  screen_dim.py --toggle        screen off / back on (what the shortcut runs)
  screen_dim.py --dim
  screen_dim.py --restore
  screen_dim.py --uninstall     remove the menu entry and the shortcut

Key names (labwc): W = Super (Windows key), C = Ctrl, A = Alt, S = Shift.
"""

import fcntl
import grp
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from xml.sax.saxutils import quoteattr

SCRIPT = Path(__file__).resolve()
HOME = Path.home()
STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(HOME / ".cache"))) / "screen-dim"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "lock"
BUS_FILE = STATE_DIR / "bus"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(HOME / ".config")))
LABWC_RC = CONFIG_DIR / "labwc" / "rc.xml"
SYSTEM_RC = Path("/etc/xdg/labwc/rc.xml")
APP_DIR = HOME / ".local" / "share" / "screen-dim"
APP_FILE = APP_DIR / "screen_dim.py"
KEY_FILE = APP_DIR / "shortcut"
DESKTOP_FILE = HOME / ".local" / "share" / "applications" / "screen-dim.desktop"
DEFAULT_KEY = "W-b"
HINT = "Screen off - press any key or click to bring it back"
MARK_START = "<!-- screen-dim -->"
MARK_END = "<!-- /screen-dim -->"


# ---------------------------------------------------------------- DDC/CI ---

def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _ddc(*args):
    """Run ddcutil on the monitor's I2C bus (cached, so calls are faster)."""
    if not shutil.which("ddcutil"):
        return 1, "ddcutil not installed"
    try:
        bus = BUS_FILE.read_text().strip()
    except OSError:
        bus = ""
    if bus:
        code, out = _run(["ddcutil", "--bus", bus, *args])
        if code == 0:
            return code, out
    code, out = _run(["ddcutil", "detect", "--brief"])
    m = re.search(r"Display \d+\s+I2C bus:\s+/dev/i2c-(\d+)", out)
    if not m:
        return 1, out
    if bus and m.group(1) == bus:
        return code, out  # same bus as before, so retrying the command won't help
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BUS_FILE.write_text(m.group(1))
    return _run(["ddcutil", "--bus", m.group(1), *args])


def get_brightness():
    """Return (current, max), or None if the monitor can't be reached over DDC/CI."""
    code, out = _ddc("getvcp", "10", "--brief")
    m = re.search(r"VCP\s+10\s+C\s+(\d+)\s+(\d+)", out)
    if code != 0 or not m:
        return None
    return int(m.group(1)), int(m.group(2))


def set_brightness(value):
    code, _ = _ddc("--noverify", "setvcp", "10", str(int(value)))
    return code == 0


# ----------------------------------------------------------------- state ---

class Lock:
    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.f = open(LOCK_FILE, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)

    def __exit__(self, *exc):
        self.f.close()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return None


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, STATE_FILE)  # atomic, so a reader never sees half a file


def clear_state():
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass


def overlay_alive(pid):
    try:
        return b"--overlay" in Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, TypeError, ValueError):
        return False


# --------------------------------------------------------------- actions ---

def dim():
    with Lock():
        state = load_state()
        if state and overlay_alive(state.get("overlay_pid")):
            return  # already off
        # If a previous dim left a saved value (overlay closed oddly), keep it.
        prev = state.get("brightness") if state else None
        if prev is None:
            b = get_brightness()
            prev = b[0] if b else None
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--overlay"],
            start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        save_state({"brightness": prev, "overlay_pid": proc.pid})
        if prev:
            set_brightness(0)


def restore(from_overlay=False):
    with Lock():
        state = load_state()
        if not state:
            return
        pid = state.get("overlay_pid")
        if not from_overlay and overlay_alive(pid):
            try:
                os.kill(int(pid), 15)  # SIGTERM closes the black window
            except OSError:
                pass
        if state.get("brightness") is not None:
            if not any(set_brightness(state["brightness"]) for _ in range(3)):
                # Keep the saved value so the next toggle/restore tries again,
                # instead of losing it and leaving the monitor at 0 for good.
                save_state({"brightness": state["brightness"], "overlay_pid": None})
                return
        clear_state()


def toggle():
    if load_state():
        restore()
    else:
        dim()


# --------------------------------------------------------------- windows ---

def pretty_key(key):
    names = {"W": "Super", "C": "Ctrl", "A": "Alt", "S": "Shift", "M": "Alt"}
    *mods, last = key.split("-")
    last = last.upper() if len(last) == 1 else last
    return "+".join([names.get(m, m) for m in mods] + [last])


def current_key():
    try:
        return KEY_FILE.read_text().strip() or DEFAULT_KEY
    except OSError:
        return DEFAULT_KEY


def run_overlay():
    import tkinter as tk

    root = tk.Tk()
    root.title("Screen Dim")
    root.configure(bg="black", cursor="none")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

    hint = tk.Label(root, text=HINT, fg="#444444", bg="black", font=("Sans", 16))
    hint.place(relx=0.5, rely=0.5, anchor="center")
    root.after(4000, hint.destroy)

    done = False

    def wake(_event=None):
        nonlocal done
        if done:
            return
        done = True
        root.withdraw()
        root.update_idletasks()
        restore(from_overlay=True)
        root.destroy()

    root.bind_all("<Key>", wake)
    root.bind_all("<Button>", wake)
    root.after(150, root.focus_force)
    root.mainloop()


def run_gui():
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        sys.exit("Tkinter is missing. Run:  python3 screen_dim.py --install")

    key = pretty_key(current_key())
    root = tk.Tk()
    root.title("Screen Dim")
    root.resizable(False, False)

    frm = ttk.Frame(root, padding=18)
    frm.grid()

    status = tk.StringVar(value="Checking the monitor…")
    ttk.Label(frm, textvariable=status, wraplength=340, justify="left").grid(
        row=0, column=0, columnspan=3, sticky="w")

    def bg(fn, then=None):
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("r", fn()), daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                root.after(100, poll)
            elif then:
                then(box.get("r"))
        poll()

    ttk.Button(frm, text="Turn screen off (keep sound)",
               command=lambda: bg(dim, lambda _r: bg(get_brightness, show))).grid(
        row=1, column=0, columnspan=3, sticky="ew", pady=(14, 6), ipady=10)

    ttk.Label(frm, text="Brightness").grid(row=2, column=0, sticky="w", pady=6)
    bright = tk.IntVar(value=0)
    scale = ttk.Scale(frm, from_=0, to=100, orient="horizontal", variable=bright,
                      length=200, state="disabled")
    scale.grid(row=2, column=1, sticky="ew", padx=8)
    value_lbl = ttk.Label(frm, width=4)
    value_lbl.grid(row=2, column=2, sticky="e")
    bright.trace_add("write", lambda *_: value_lbl.config(text=str(bright.get())))
    scale.bind("<ButtonRelease-1>", lambda _e: bg(lambda: set_brightness(bright.get())))

    def show(b):
        if load_state():
            status.set(f"The screen is off. Press Restore, any key, or {key}.")
        elif b:
            cur, mx = b
            scale.config(to=mx, state="normal")
            bright.set(cur)
            status.set(f"Monitor found (DDC/CI). Brightness {cur} of {mx}.")
        else:
            status.set("Brightness control isn't available (turn on DDC/CI in the "
                       "monitor's menu for a darker screen). The screen will still "
                       "be covered in black and the sound keeps playing.")

    def do_restore():
        bg(restore, lambda _r: bg(get_brightness, show))

    ttk.Button(frm, text="Restore", command=do_restore).grid(
        row=3, column=0, columnspan=3, sticky="ew", pady=(6, 10))

    ttk.Label(frm, foreground="#777777",
              text=f"Bring the screen back: any key, a click, or {key}").grid(
        row=4, column=0, columnspan=3, sticky="w")

    bg(get_brightness, show)
    root.mainloop()


# ----------------------------------------------------- shortcut & install ---

def _strip_block(text):
    return re.sub(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?",
                  "", text, flags=re.S)


def _reload_labwc():
    code, _ = _run(["pkill", "-HUP", "-u", str(os.getuid()), "-x", "labwc"])
    if code == 0:
        print("   Desktop reloaded - the shortcut works now.")
    else:
        print("   labwc isn't running in this session; log out and back in, "
              "or check that your desktop uses labwc.")


def install_shortcut(key=DEFAULT_KEY, script=SCRIPT):
    LABWC_RC.parent.mkdir(parents=True, exist_ok=True)
    if LABWC_RC.exists():
        text = LABWC_RC.read_text()
        bak = LABWC_RC.with_name("rc.xml.bak")
        if not bak.exists():
            shutil.copy2(LABWC_RC, bak)
    elif SYSTEM_RC.exists():
        text = SYSTEM_RC.read_text()  # start from the Pi's defaults so none are lost
    else:
        text = '<?xml version="1.0"?>\n<labwc_config>\n</labwc_config>\n'

    text = _strip_block(text)
    # labwc only loads its built-in shortcuts (Alt+Tab, ...) when rc.xml has no
    # <keybind> of its own, or has <default />. Keep them when adding ours.
    needs_default = "<keybind" not in text and "<default" not in text
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} --toggle"
    block = (f"{MARK_START}\n" + ("<default />\n" if needs_default else "") +
             f"<keybind key={quoteattr(key)}><action name=\"Execute\" "
             f"command={quoteattr(command)}/></keybind>\n{MARK_END}\n")

    if "</keyboard>" in text:
        text = text.replace("</keyboard>", block + "</keyboard>", 1)
    else:
        ends = list(re.finditer(r"</(labwc_config|openbox_config)>", text))
        if not ends:
            sys.exit(f"Couldn't read {LABWC_RC}; add the shortcut by hand.")
        i = ends[-1].start()
        text = text[:i] + "<keyboard>\n" + block + "</keyboard>\n" + text[i:]

    LABWC_RC.write_text(text)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key)
    print(f"   Shortcut {pretty_key(key)} added to {LABWC_RC}")
    _reload_labwc()


def _sudo(*cmd):
    print("   $ sudo " + " ".join(cmd))
    return subprocess.run(["sudo", *cmd]).returncode == 0


def _in_group(user, group):
    try:
        g = grp.getgrnam(group)
    except KeyError:
        return False
    return user in g.gr_mem or os.getgid() == g.gr_gid


def install(key=DEFAULT_KEY):
    if os.geteuid() == 0:
        sys.exit("Run it as your normal user (no sudo) - it asks for the password when needed.")
    user = os.environ.get("USER") or Path.home().name

    print("== 1/5 Installing packages (ddcutil, python3-tk)")
    _sudo("apt-get", "update")
    if not _sudo("apt-get", "install", "-y", "ddcutil", "python3-tk"):
        sys.exit("apt failed - check the internet connection and try again.")

    print("== 2/5 Enabling brightness control (DDC/CI over I2C)")
    _sudo("sh", "-c", "echo i2c-dev > /etc/modules-load.d/i2c-dev.conf")
    _sudo("modprobe", "i2c-dev")
    new_group = False
    if not _in_group(user, "i2c"):
        new_group = _sudo("usermod", "-aG", "i2c", user)

    print("== 3/5 Turning off screen blanking (it switches HDMI off and cuts the sound)")
    if shutil.which("raspi-config"):
        _sudo("raspi-config", "nonint", "do_blanking", "1")
    else:
        print("   raspi-config not found - turn off screen blanking by hand.")

    print("== 4/5 Adding Screen Dim to the menu")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if SCRIPT != APP_FILE.resolve():
        shutil.copy2(SCRIPT, APP_FILE)
    APP_FILE.chmod(0o755)
    DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_FILE.write_text(
        "[Desktop Entry]\n"
        "Name=Screen Dim\n"
        "Comment=Turn the screen off and keep the music playing\n"
        f'Exec=python3 "{APP_FILE}"\n'
        "Icon=video-display\n"
        "Terminal=false\n"
        "Type=Application\n"
        "Categories=Utility;\n")

    print("== 5/5 Adding the keyboard shortcut")
    install_shortcut(key, APP_FILE)

    print(f"\nDone. Open it from Menu > Accessories > Screen Dim, "
          f"or press {pretty_key(key)} to turn the screen off and on.")
    if new_group:
        print("Log out and back in (or reboot) once so brightness control works.")


def uninstall():
    if LABWC_RC.exists():
        LABWC_RC.write_text(_strip_block(LABWC_RC.read_text()))
        print("   Shortcut removed")
        _reload_labwc()
    restore()
    for f in (DESKTOP_FILE, APP_FILE, KEY_FILE):
        try:
            f.unlink()
            print(f"   Removed {f}")
        except FileNotFoundError:
            pass
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    print("Screen Dim removed (ddcutil and python3-tk were left installed).")


# ------------------------------------------------------------------ main ---

def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    arg = args[1] if len(args) > 1 else DEFAULT_KEY
    if cmd == "--install":
        install(arg)
    elif cmd == "--uninstall":
        uninstall()
    elif cmd == "--install-shortcut":
        install_shortcut(arg)
    elif cmd == "--toggle":
        toggle()
    elif cmd == "--dim":
        dim()
    elif cmd == "--restore":
        restore()
    elif cmd == "--overlay":
        run_overlay()
    elif cmd in ("-h", "--help"):
        print(__doc__)
    else:
        run_gui()


if __name__ == "__main__":
    main()
