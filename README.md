# Screen Dim for Raspberry Pi 5

Blacks out the monitor but keeps HDMI sound playing. The HDMI signal stays on,
so the monitor's speakers keep working. The screen is covered with a black
window and, if the monitor supports DDC/CI, its brightness is turned down to 0.
The previous brightness is put back when you restore.

Made for Raspberry Pi OS (Bookworm or newer) with the labwc desktop.

## Install

Open a terminal on the Pi and run this as your normal user (not with `sudo`;
it asks for your password when it needs it):

```bash
curl -fsSL https://raw.githubusercontent.com/luis199421/pi-5-dimming/main/screen_dim.py -o /tmp/screen_dim.py && python3 /tmp/screen_dim.py --install
```

To use another shortcut than Super+B, put it at the end. For example, Ctrl+Alt+S:

```bash
curl -fsSL https://raw.githubusercontent.com/luis199421/pi-5-dimming/main/screen_dim.py -o /tmp/screen_dim.py && python3 /tmp/screen_dim.py --install C-A-s
```

Key names: `W` = Super (Windows key), `C` = Ctrl, `A` = Alt, `S` = Shift.

The installer:

1. installs `ddcutil` and `python3-tk`
2. turns on I2C so the monitor's brightness can be controlled (DDC/CI)
3. turns off screen blanking, because blanking switches HDMI off and cuts the sound
4. adds **Screen Dim** to Menu > Accessories
5. adds the keyboard shortcut

If it says you were added to the `i2c` group, log out and back in once.

## Use

- Press **Super+B** (or your shortcut) to turn the screen off, and again to bring it back.
- While the screen is off, any key or mouse click also brings it back.
- Or open **Menu > Accessories > Screen Dim** for buttons and a brightness slider.

From the terminal:

```bash
~/.local/share/screen-dim/screen_dim.py --toggle
```

Other options: `--dim`, `--restore`, `--help`.

For a darker screen, turn on **DDC/CI** in your monitor's own menu. Without it
the screen is still covered in black, but the backlight stays on.

## Update

Run the install command again.

## Uninstall

```bash
python3 ~/.local/share/screen-dim/screen_dim.py --uninstall
```

`ddcutil` and `python3-tk` are left installed.
