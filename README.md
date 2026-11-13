# TI-84 Plus CE Remote Desktop

Use a TI-84 Plus CE as a remote desktop client for your computer: the
calculator shows the computer's screen and its keypad drives the computer's
mouse and keyboard, over the ordinary USB cable.

```
 ┌──────────── PC ────────────┐      USB cable       ┌──── TI-84 Plus CE ────┐
 │ host/  (Python)            │   (USB serial, CDC)  │ calc/  (C, REMOTE.8xp) │
 │  capture screen → 320x240  │ ───── frames ──────▶ │  draw to the LCD       │
 │  move mouse / press keys   │ ◀──── keypad ─────── │  scan the keypad       │
 └────────────────────────────┘                      └────────────────────────┘
```

The calculator has no network hardware, so the link is the USB cable: while
`REMOTE` runs, the calculator presents itself as a USB serial port. The host
scales the desktop to the 320x240 screen, converts it to 256 colours and sends
only the pixels that changed. The calculator sends its raw key state and the
host decides what each key means, so the key map is editable without
rebuilding the calculator program.

There are two ways to connect, using the same calculator program:

- **USB cable** straight to the computer — described on this page.
- **Wi-Fi**, through an ESP32-S3 plugged into the calculator's USB port — see
  [`bridge/`](bridge/README.md).

> **Status:** the host is covered by automated tests (including an end-to-end
> run against a simulated calculator), and the calculator's frame decoder is
> tested natively against the host's encoder. The calculator program has not
> been run on physical hardware yet — please report what you see.

## 1. Put the program on the calculator

1. Get `REMOTE.8xp`: download the **REMOTE** artifact from the latest run of
   the *Build* workflow on this repository's **Actions** tab, or build it
   yourself (below).
2. Install the [CE C libraries](https://github.com/CE-Programming/libraries/releases/latest)
   (`clibs.8xg`) on the calculator. `REMOTE` needs the USB, serial, graphics
   and keypad libraries from it.
3. Send both files with [TI Connect CE](https://education.ti.com/en/products/computer-software/ti-connect-ce-sw)
   (or TILP).
4. Calculator OS 5.5 and later block native programs; install
   [arTIfiCE](https://yvantt.github.io/arTIfiCE/) to run them. On older OS
   versions run `Asm(prgmREMOTE)` or just `prgmREMOTE`.

Building it yourself needs the [CE C toolchain](https://ce-programming.github.io/toolchain/static/getting-started.html)
(v15 or newer):

```sh
make -C calc        # produces calc/bin/REMOTE.8xp
```

## 2. Run the host on the computer

Python 3.9+ on Windows, macOS or Linux:

```sh
cd host
pip install -r requirements.txt
python -m tiremote
```

Then plug in the calculator and start `REMOTE`. The host finds the
calculator's serial port by itself and starts streaming; if you start things in
a different order, or unplug and replug, both sides reconnect on their own.

| Option | |
| --- | --- |
| `--monitor N` | which monitor to share (1 = primary, 0 = all of them) |
| `--fps N` | frame rate cap (default 20) |
| `--view-only` | show the screen but ignore the calculator's keys |
| `--dither` | dithered colours: smoother gradients, more data per frame |
| `--filter NAME` | downscaling filter: `lanczos` (default), `bilinear`, `box`, `nearest` |
| `--listen [PORT]` | network mode: wait for the [Wi-Fi bridge](bridge/README.md) instead of a cable (needs `--key` or `TIREMOTE_KEY`) |
| `--port NAME` | use a specific serial port instead of auto-detecting |
| `--list-ports` | show serial ports and which one is the calculator |
| `--snapshot FILE` | save a picture of what the calculator would show, then exit |

Platform notes:

- **Windows 10/11:** the calculator appears as a COM port with the built-in
  driver. Close TI Connect CE first if it is holding the device.
- **macOS:** grant your terminal *Screen Recording* and *Accessibility*
  permission (System Settings → Privacy & Security), otherwise the picture is
  blank and input is ignored.
- **Linux:** needs an X11 session (Wayland blocks screen capture and input
  injection for ordinary programs). Add yourself to the `dialout` group (or
  your distribution's equivalent) for access to `/dev/ttyACM*`.

## 3. Controls

A whole desktop does not fit legibly in 320x240, so the view zooms: **fit**
shows everything, and each press of `[zoom]` moves closer until one screen
pixel is one calculator pixel. Zoomed views follow the mouse pointer. The
status line in the bottom-left corner shows the current layer, arrow mode and
zoom.

| Calculator key | Action |
| --- | --- |
| Arrows | Move the mouse (tap = one pixel, hold = glide and accelerate) |
| `[y=]` | Left mouse button (hold to drag) |
| `[window]` | Right mouse button |
| `[sto→]` | Middle mouse button |
| `[trace]` / `[graph]` | Scroll up / down |
| `[zoom]` | Zoom in; `[2nd]`+`[zoom]` zooms out |
| `[mode]` | Switch the arrows between mouse movement and the arrow keys |
| `[alpha]` | Toggle the letter layer (the green letters above the keys) |
| `[2nd]` | Shift (hold) |
| `[vars]` / `[stat]` | Ctrl / Alt (hold) |
| `[enter]` `[del]` `[clear]` `[X,T,θ,n]` | Enter, Backspace, Escape, Tab |
| `[math]` `[apps]` `[prgm]` | Windows/Command key, Space, Delete (number layer) |
| `0`–`9` `.` `,` `(` `)` `+` `-` `×` `÷` `^` | Themselves |
| `[x⁻¹]` `[sin]` `[cos]` `[tan]` `[x²]` `[log]` `[ln]` `[(-)]` | `'` `[` `]` `=` `;` `\` `` ` `` `_` |
| **`[2nd]`+`[mode]`** | **Quit** the calculator program |

With the letter layer on, the keys type their green letter (`[math]` = a,
`[apps]` = b, `[prgm]` = c, …, `0` = space) and every key without a letter
keeps its job. `[2nd]` shifts whatever is typed (`a` → `A`, `1` → `!`,
`;` → `:` and so on, US layout), which covers the whole keyboard. Shortcuts
work by holding the modifier: Ctrl+C is hold `[vars]` and tap `[prgm]` in the
letter layer; Alt+Tab is hold `[stat]` and tap `[X,T,θ,n]`.

To change any of this, edit the two dictionaries at the top of
`host/tiremote/keymap.py`; the calculator program does not need rebuilding.

## How it works

- **Transport:** `srldrvce` makes the calculator a USB CDC ACM device
  (VID `16C0`, PID `05E1`). Baud rate is irrelevant.
- **Flow control:** the calculator asks for each frame (`REQ`), so frames
  never queue up and the picture is always the newest one. If a reply stops
  arriving for 1.5 s the calculator drains its input and sends `HELLO`, which
  makes the host resend the palette and a full frame. That one rule handles
  startup order, replugging and corrupted data.
- **Frames:** 320x240, 8 bits per pixel, fixed palette (a 6x8x5 colour cube
  plus 16 greys). Each frame is a list of `skip` / `fill` / `copy` operations
  against the previous frame, which the calculator decodes with nothing more
  than `memset` and `memcpy`. A static screen costs 2 bytes per frame.
- **Input:** the calculator sends its 7-byte key matrix whenever it changes.
  Pointer acceleration, key repeat, layers and zoom all live in the host.

The protocol constants are in `calc/src/proto.h` (mirrored in
`host/tiremote/protocol.py`).

## Development

```sh
cd host
pip install -r requirements.txt pytest
xvfb-run -a python -m pytest tests     # Linux; the end-to-end tests need an X display
```

Set `MINIZ_DIR` to a checkout of [miniz](https://github.com/richgel999/miniz)
to also run the tests that compile the bridge's protocol code natively.

`calc/src/decode.c` is deliberately free of calculator headers so the tests
can compile it natively and check it against the Python encoder.

## Limits worth knowing

- Frame rate depends on how much of the screen changes: USB full-speed serial
  and a 48 MHz eZ80 manage smooth updates for typing and pointer movement,
  but full-screen video will be a slideshow.
- The pointer is drawn by the host; the calculator has no local cursor, so it
  moves at the frame rate.
- The calculator has no Wi-Fi or Bluetooth of its own; going wireless needs
  the ESP32 bridge in [`bridge/`](bridge/README.md).
