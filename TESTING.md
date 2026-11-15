# Testing checklist

What has changed since the last time the calculator was updated, how to
install it, and what to try. Everything here passes the automated tests but
has **not** been run on a real calculator yet, so each item says what
"working" looks like and what to send back if it isn't.

## Current status: the newest calculator program crashes

The newest `REMOTE.8xp` crashes the calculator as soon as the first picture
arrives (the calculator resets and shows "Validating OS" on restart). The
cause is not known yet. Until it is fixed:

- **To keep using it:** install the last known-good calculator program from
  [this build](https://github.com/grahamrobbins1234/Calc/actions/runs/43513640694).
  The current host works with it.
- **To help find the cause:** try the calculator program from
  [this in-between build](https://github.com/grahamrobbins1234/Calc/actions/runs/43612285125)
  once, with the current host, and report whether it crashes. That single
  answer says which half of the recent changes is at fault.

The checklist below applies once a fixed build is out.

## 1. Update both sides

**Calculator**

1. Open the [Build workflow runs](https://github.com/grahamrobbins1234/Calc/actions/workflows/build.yml?query=branch%3Aclaude%2Fti84-remote-desktop-client-y673gj)
   and pick the newest one with a green tick.
2. Download the **REMOTE** artifact, unzip it, and send `REMOTE.8xp` to the
   calculator with TI Connect CE, replacing the old one.
3. Quit TI Connect CE.

**Mac**

```sh
cd ~/Calc && git pull
source ~/calcenv/bin/activate
pip install -r host/requirements.txt
cd host
python -m tiremote --stats
```

Then start `REMOTE` on the calculator (`[apps]` → Cabri Jr → Open → ARTIFICE).

`--stats` prints a line every 5 seconds such as
`12.4 fps, 85 KB/s, 256c half-size`. Those lines are the most useful thing to
send back.

## 2. Quick health check

| Try | Expect |
| --- | --- |
| Start the host, then `REMOTE` | "Connected", then "Calculator connected, streaming." No version warning. |
| Move the pointer with the arrows, click with `[y=]` | Pointer follows; clicks land where the pointer is |
| Hold an arrow until the pointer reaches an edge | It stops at the edge of the screen instead of disappearing |
| `[zoom]`, then move around | Picture slides smoothly in every direction, no flashes of the waiting screen |

If the waiting screen flashes, note the **"Last link error code"** number it
shows.

## 3. New things to test

Work through these in order; each is independent.

### A. Command key on the Mac

`[vars]` is now Command and `[math]` is Control.

- Press `[alpha]` to turn letters on, then hold `[vars]` and tap `[prgm]`
  (the letter C). That should copy; hold `[vars]` and tap `[6]` (V) to paste.
- **Report:** whether shortcuts work while letters are on.

### B. Big scene changes

Switch between two very different windows, or open a full-screen picture.

- **Expect:** the new scene appears almost at once, blurry, then sharpens
  about half a second after it stops changing.
- **Report:** whether that feels faster than before, and whether the
  sharpening step leaves any wrong-looking patches.

### C. 3D game or video

Run something where the whole picture keeps changing, for 20 seconds or more.

- **Expect:** the picture goes blurry (half size) immediately. If the link
  manages fewer than 8 frames a second, it steps down again to a very blocky
  picture (quarter size). When you pause, it sharpens.
- **Report:** a few `--stats` lines from during the game. They say which size
  it settled on and the frame rate.
- **Then try**, for comparison, each of these and note the stats:
  - `[2nd]`+`[clear]` to switch to 16 colours (halves the data again)
  - `[2nd]`+`[del]` to cycle video mode: auto → half → quarter → off
  - starting with `--video-fps 12` (steps down to quarter size sooner) or
    `--video-fps 4` (stays at half size longer)

### D. Game colour mode (works with the known-good calculator program)

This one is a host-only change, so it can be tried right now with the
known-good calculator program.

```sh
python -m tiremote --stats --colors 16a
python -m tiremote --stats --colors 16a --game-colors 6
```

Open PolyTrack and drive. The second command limits the mode to six colours
(the status line shows `6a`): flatter and plainer, but less data still.

- **Expect:** the game keeps its look (blue sky, green hills, grey track,
  red and white kerbs) with visibly banded sky, and a higher frame rate than
  256 colours. Changing track or opening a menu may cause one full redraw
  as new colours are picked.
- **Report:** `--stats` lines with `--colors 16a` and with the default 256
  colours, and whether the colours ever look wrong for more than a couple
  of seconds.

### E. Link speed: new compression

This only shows up in the numbers. Do the same activity twice, for
example scrolling a web page up and down for 20 seconds while zoomed in:

```sh
python -m tiremote --stats
python -m tiremote --stats --no-history
```

- **Expect:** the first run shows a higher frame rate, or the same frame
  rate at fewer KB/s.
- **Report:** a few stats lines from each run.

## 4. If something goes wrong

| Symptom | Try | Send back |
| --- | --- | --- |
| Garbled picture, or the waiting screen keeps flashing | Add `--no-history`. If still bad, also add `--no-lz4` | Which option fixed it, and the "Last link error code" |
| Stuck on "Waiting for host" | Check the host says "Connected"; unplug and replug | What the host printed |
| Host says the calculator's version is too old | Re-send `REMOTE.8xp` from the newest build | — |
| Blocky picture never sharpens | `[2nd]`+`[del]` until the status line says `video:off` | What was on screen at the time |
| Colours wrong in 16-colour, 16-grey or black-and-white mode | `[2nd]`+`[clear]` back to 256 colours | Which mode, and a photo |

## 5. Results to send back

The short version, if you only have a minute:

1. Stats lines from normal desktop use.
2. Stats lines from a game, saying whether it was half or quarter size.
3. Stats lines with and without `--no-history`.
4. Anything from sections A–D that didn't behave as described.
