# Testing checklist

What has changed since the last time the calculator was updated, how to
install it, and what to try. Everything here passes the automated tests but
has **not** been run on a real calculator yet, so each item says what
"working" looks like and what to send back if it isn't.

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
python -m tiremote --rotate 90 --screen-size 956x1470 --stats
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

### A. Pointer turned to match the rotated picture

With `--rotate 90`, the arrow pointer should now point the right way up as
you hold the calculator, not lie on its side.

- **Report:** whether it looks right, and whether the tip is where clicks land.

### B. Command key on the Mac

`[vars]` is now Command and `[math]` is Control.

- Press `[alpha]` to turn letters on, then hold `[vars]` and tap `[prgm]`
  (the letter C). That should copy; hold `[vars]` and tap `[6]` (V) to paste.
- **Report:** whether shortcuts work while letters are on.

### C. Automatic screen-size detection

Stop the host and run it once **without** `--screen-size`:

```sh
python -m tiremote --rotate 90 --stats
```

- **Working:** the first line says `Sharing monitor 1 (956x1470, rotated display detected)`.
- **Not working:** it says `1470x956` and the picture is cut off. Go back to
  using `--screen-size 956x1470`, and send the output of
  `python -m tiremote --displays`.

### D. Big scene changes

Switch between two very different windows, or open a full-screen picture.

- **Expect:** the new scene appears almost at once, blurry, then sharpens
  about half a second after it stops changing.
- **Report:** whether that feels faster than before, and whether the
  sharpening step leaves any wrong-looking patches.

### E. 3D game or video

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

### F. Link speed: new compression and larger USB buffer

These two only show up in the numbers. Do the same activity twice, for
example scrolling a web page up and down for 20 seconds while zoomed in:

```sh
python -m tiremote --rotate 90 --screen-size 956x1470 --stats
python -m tiremote --rotate 90 --screen-size 956x1470 --stats --no-history
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
| Picture cut off or the wrong shape | Add `--screen-size 956x1470` | Output of `python -m tiremote --displays` |
| Colours wrong in 16-colour, 16-grey or black-and-white mode | `[2nd]`+`[clear]` back to 256 colours | Which mode, and a photo |

## 5. Results to send back

The short version, if you only have a minute:

1. Stats lines from normal desktop use.
2. Stats lines from a game, saying whether it was half or quarter size.
3. Stats lines with and without `--no-history`.
4. Anything from sections A–E that didn't behave as described.
