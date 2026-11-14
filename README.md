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

> **Status:** the USB cable setup has been run on a real TI-84 Plus CE
> Python Edition with a Mac as the host. The Wi-Fi bridge compiles and its
> protocol code is tested, but it has not been run on hardware. Everything is
> covered by automated tests, including the calculator's decoding code
> compiled natively and checked against the host.

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
| `--rotate DEG` | turn the picture 90, 180 or 270 degrees clockwise, for holding the calculator sideways; the arrow keys turn with it |
| `--screen-size WxH` | override the detected monitor size, e.g. `956x1470`, if a rotated display is shown cut off |
| `--displays` | print what the system reports about the displays, for troubleshooting |
| `--fps N` | frame rate cap (default 20) |
| `--view-only` | show the screen but ignore the calculator's keys |
| `--colors N` | starting colour mode: `256` (default), `16c` (16 colours), `16` (16 greys) or `2` (black and white) |
| `--video MODE` | low-resolution video mode: `auto` (default), `half`, `quarter` or `off` |
| `--video-fps N` | in automatic video mode, step down to quarter size below this frame rate (default 8) |
| `--dither` | dithered colours: smoother gradients, more data per frame |
| `--stats [SECONDS]` | print frame rate and data rate every few seconds while running |
| `--no-history` | compress each packet on its own, for comparing speeds |
| `--no-lz4` | do not LZ4-compress the stream (if the calculator turns out faster without) |
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
status line in the bottom-left corner shows the current layer, arrow mode,
colour mode and zoom.

**Video mode** is for games, video and big scene changes, where most of the
picture changes at once. Instead of making you wait for a full-detail frame,
the host sends a small picture that the calculator enlarges, and follows it
with the sharp one once things settle for about half a second:

- **Half size** (160x120): a quarter of the data per frame. A big change
  such as switching windows appears at once, blurry, then sharpens.
- **Quarter size** (80x60): a sixteenth of the data. In automatic mode the
  host steps down to this only when it measures that half size is getting
  fewer than 8 frames a second through the link (`--video-fps` changes
  that), which is what fast 3D games tend to need.

Text is not readable while it is active, which is why it is automatic.
`[2nd]`+`[del]` cycles auto → always half → always quarter → off.

If updates feel slow, drop the colour depth with `[2nd]`+`[clear]`: 16
colours or 16 greys send half the data of 256 colours, and black-and-white
an eighth. Text stays readable in all of them. The 16-colour palette is
coarse, so photos and games band into flat patches; `--dither` smooths that
at the cost of some of the saving.

| Calculator key | Action |
| --- | --- |
| Arrows | Move the mouse (tap = one pixel, hold = glide and accelerate) |
| `[y=]` | Left mouse button (hold to drag) |
| `[window]` | Right mouse button |
| `[sto→]` | Middle mouse button |
| `[trace]` / `[graph]` | Scroll up / down |
| `[zoom]` | Zoom in; `[2nd]`+`[zoom]` zooms out |
| `[2nd]`+`[clear]` | Cycle colour mode: 256 colours → 16 colours → 16 greys → black and white |
| `[2nd]`+`[del]` | Cycle video mode: auto → half size → quarter size → off |
| `[mode]` | Switch the arrows between mouse movement and the arrow keys |
| `[alpha]` | Toggle the letter layer (the green letters above the keys) |
| `[2nd]` | Shift (hold) |
| `[vars]` / `[stat]` | Ctrl / Alt (hold). On a Mac `[vars]` is Command instead |
| `[enter]` `[del]` `[clear]` `[X,T,θ,n]` | Enter, Backspace, Escape, Tab |
| `[math]` `[apps]` `[prgm]` | Windows key (Control on a Mac), Space, Delete (number layer) |
| `0`–`9` `.` `,` `(` `)` `+` `-` `×` `÷` `^` | Themselves |
| `[x⁻¹]` `[sin]` `[cos]` `[tan]` `[x²]` `[log]` `[ln]` `[(-)]` | `'` `[` `]` `=` `;` `\` `` ` `` `_` |
| **`[2nd]`+`[mode]`** | **Quit** the calculator program |

With the letter layer on, the keys type their green letter (`[math]` = a,
`[apps]` = b, `[prgm]` = c, …, `0` = space) and every key without a letter
keeps its job. `[2nd]` shifts whatever is typed (`a` → `A`, `1` → `!`,
`;` → `:` and so on, US layout), which covers the whole keyboard. Shortcuts
work by holding the modifier: Ctrl+C (Command+C on a Mac) is hold `[vars]`
and tap `[prgm]` in the letter layer; Alt+Tab is hold `[stat]` and tap
`[X,T,θ,n]`.

To change any of this, edit the two dictionaries at the top of
`host/tiremote/keymap.py`; the calculator program does not need rebuilding.

## How it works

- **Transport:** `srldrvce` makes the calculator a USB CDC ACM device
  (VID `16C0`, PID `05E1`). Baud rate is irrelevant.
- **Flow control:** the host may have two frames sent but not yet
  acknowledged, so one frame travels while the calculator draws the previous
  one, and nothing piles up beyond that. When the screen is idle the host
  sends an empty frame twice a second as a heartbeat.
- **Recovery:** if the calculator hears nothing for 1.5 s, or receives
  anything invalid, it drains its input and sends `HELLO`; the host answers
  with the screen mode, the palette and a full frame. That one rule handles
  startup order, replugging and corrupted data.
- **Frames:** 320x240 at 8, 4 or 1 bits per pixel. Each frame is a list of
  `skip` / `fill` / `copy` operations against the previous frame, plus
  `move` operations that shift a block of the picture up, down or sideways.
  The host uses them when something scrolls and when a zoomed view pans to
  follow the pointer, so only the newly exposed strip is sent.
- **Video mode:** in the low-resolution modes frames describe a 160x120 or
  80x60 picture kept in spare video memory; after each frame the calculator
  redraws just the rows that changed, enlarging every pixel. When the host
  returns to full size it sends the sharp picture as changes to the enlarged
  one already on screen, so flat areas are not sent again.
- **Compression:** host data travels in packets of up to 8 KB, each
  LZ4-compressed when that makes it smaller. A packet may copy from the
  24 KB of data sent before it, which the calculator keeps in a sliding
  window, so content that was on screen a moment ago costs little to show
  again. On a recorded scroll-and-page-switch session this cut the data by
  about a third in the colour modes. The buffers live in the unused half of
  the calculator's video memory.
- **Pointer:** the calculator draws the mouse pointer itself. The host sends
  its position as a tiny out-of-band packet whenever it moves. These may
  arrive between the chunks of a large frame and are acted on at once, so
  the pointer keeps moving while a big update is still coming in.
- **Input:** the calculator scans its keys every 5 ms, even while picture
  data is pouring in, and sends its 7-byte key matrix whenever it changes.
  Pointer acceleration, key repeat, layers and zoom all live in the host.

Measured on a real browser window being scrolled (1:2 zoom, average bytes
per changed frame, PC side only — the calculator's speed is still unmeasured):

| Colour mode | Plain delta | + scroll moves | + LZ4 |
| --- | --- | --- | --- |
| 256 colours | 17.6 KB | 5.1 KB | 3.9 KB |
| 16 greys | 9.4 KB | 2.7 KB | 2.6 KB |
| Black and white | 2.1 KB | 0.6 KB | 0.6 KB |

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

`calc/src/decode.c` (frame decoder, packet reader, LZ4, pointer drawing) is
deliberately free of calculator headers so the tests can compile it natively,
with sanitizers on, and check it against the Python side.

## Limits worth knowing

- Frame rate depends on how much of the screen changes: USB full-speed serial
  and a 48 MHz eZ80 manage smooth updates for typing and pointer movement,
  but full-screen video will be a slideshow.
- The pointer disappears briefly while a large update is being drawn under
  it.
- In the 16-grey and black-and-white modes the order of pixels within each
  byte is taken from the LCD controller's documentation and has not been
  seen on a real screen. If those modes look scrambled in vertical stripes,
  that is the cause; please report it.
- The host works with the current calculator program and the three before
  it, and says so if the calculator's is too old. Older ones miss the newest
  features: the turned pointer for rotated pictures, quarter-size video, and
  compression that refers back to earlier data.
- If the calculator ever rejects what the host sent, it briefly shows its
  waiting screen with a "Last link error code". That number says what was
  wrong (the list is in `calc/src/decode.h`) and is worth including in a bug
  report.
- The calculator has no Wi-Fi or Bluetooth of its own; going wireless needs
  the ESP32 bridge in [`bridge/`](bridge/README.md).
