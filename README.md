# BazziteScreenshot

A Print Screen tool for **Bazzite** (KDE Plasma 6 on Wayland). Press
<kbd>Print</kbd> and the screen freezes. You can then drag out a region or pick
an application window, and click **Copy**. The image goes straight to the
clipboard.

- **The frozen frame is taken the moment you press Print.** Every monitor is
  captured at native resolution through KWin in about 50 ms, before the
  overlay appears. Animations, videos and tooltips stay exactly as they were
  when you pressed the key.
- **Region mode:** drag a rectangle, then fine-tune it with the handles or
  move it. The label shows the real pixel size of the output.
- **Window mode:** press <kbd>W</kbd> or click **Window**, hover to highlight
  an application, and click it. You can also pick any open app, a single
  screen, or all screens from the **Select an application...** dropdown.
- **Multi-monitor and fractional scaling:** selections can span screens with
  different scale factors.
- **Always ready:** a small user service starts at login and waits on D-Bus.
  It uses no CPU while idle. It's a resident daemon because starting Python
  and Qt on every key press would add about a second of lag. That would break
  the "freeze at the moment of the key press" guarantee.

## Install

Bazzite already ships everything this needs (`python3-pyside6`,
`python3-dbus`, `wl-clipboard`). Nothing is layered with rpm-ostree, and no
root access is required.

```bash
git clone https://github.com/ReOp14/BazziteScreenshot.git
cd BazziteScreenshot
./install.sh
```

The installer:

1. Copies the app to `~/.local/share/bazzite-screenshot`, with a CLI wrapper
   at `~/.local/bin/bazzite-screenshot`.
2. Installs and enables the `bazzite-screenshot.service` systemd user unit, so
   it starts with every graphical login. It also installs a D-Bus activation
   file, so the first key press works even if the service was stopped.
3. Binds <kbd>Print</kbd> through KDE's global shortcut service. If another
   app, such as Spectacle or Flameshot, has Print, it's removed from that app.
   You can change the key later in *System Settings > Keyboard > Shortcuts >
   BazziteScreenshot*.

Uninstall with `./uninstall.sh`.

## Usage

| Action | How |
| --- | --- |
| Take a screenshot | <kbd>Print</kbd> |
| Select a region | drag; adjust with handles; drag inside to move |
| Select a window | <kbd>W</kbd> / **Window**, then click a window, or use the dropdown |
| Back to region mode | <kbd>R</kbd> / **Region** (or just start dragging) |
| Copy to clipboard | **Copy**, <kbd>Enter</kbd>, <kbd>Ctrl</kbd>+<kbd>C</kbd>, or double-click the selection |
| Clear selection / cancel | right-click / <kbd>Esc</kbd> |

After copying, paste the screenshot anywhere with <kbd>Ctrl</kbd>+<kbd>V</kbd>.

The CLI:

```bash
bazzite-screenshot trigger              # same as pressing Print
bazzite-screenshot register-shortcut    # re-bind Print
bazzite-screenshot unregister-shortcut
bazzite-screenshot daemon --no-notify   # run in the foreground without notifications
```

## How it works

```
Print ─► kglobalaccel ─► busctl … Trigger ─► daemon (io.github.bazzitescreenshot)
                                               ├─ KWin ScreenShot2: CaptureScreen per monitor (frozen frame)
                                               ├─ temporary KWin script: window list + cursor → WindowList()
                                               └─ fullscreen overlay per monitor → Copy → wl-copy / Qt clipboard
```

- KWin only allows executables that are whitelisted in a `.desktop` file
  (`X-KDE-DBUS-Restricted-Interfaces`) to take screenshots. Whitelisting
  `/usr/bin/python3` would let *every* Python script read your screen. Instead,
  the daemon runs on a private copy of the interpreter
  (`~/.local/share/bazzite-screenshot/bin/bazzite-screenshot-python`), and only
  that copy is whitelisted. An `ExecStartPre` hook re-copies it after Bazzite
  updates Python. If KWin ever refuses the call, the daemon falls back to
  `spectacle --background`.
- Window picking crops the frozen frame to the window's geometry. You get
  exactly what was on screen when you pressed Print, including anything that
  was overlapping the window at the time.

## Troubleshooting

- **Nothing happens on Print:** run `systemctl --user status bazzite-screenshot`
  and `journalctl --user -u bazzite-screenshot`. Then try
  `bazzite-screenshot trigger`. If that works, re-run
  `bazzite-screenshot register-shortcut`.
- **Spectacle still opens:** check *System Settings > Keyboard > Shortcuts* for
  a leftover Print binding, then re-run `bazzite-screenshot register-shortcut`.
- **Game Mode (Steam gamescope session):** not supported. Gamescope is a
  different compositor, and this tool needs the Plasma desktop session.

## License

MIT
