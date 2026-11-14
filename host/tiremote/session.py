"""One connected calculator: streams frames and applies its key presses.

Flow control is a small window: the host may have FRAME_WINDOW frames sent
but not yet acknowledged, so one frame can be on the wire while the
calculator draws the previous one. Pointer positions are tiny separate
messages sent as the pointer moves, independent of frames.
"""

import collections
import queue
import struct
import threading
import time

import numpy as np
import serial

from .encoder import (apply_move, cursor_packet, encode_frame, move_is_valid, move_op,
                      packets)
from .palette import MODE_ORDER, MODES, scaled_mode
from .protocol import (CURSOR_HIDDEN, FRAME_WINDOW, KEY_GROUPS, MSG_ACK, MSG_BYE,
                       MSG_HELLO, MSG_KEYS, COMPATIBLE_VERSIONS, PROTO_VERSION)
from .scroll import find_scroll, pan_move

KEEPALIVE = 0.5  # send something at least this often so the calculator knows we are here

# Video mode: send a small picture that the calculator enlarges, for content
# that changes all over (games, video, big scene changes), then the sharp
# picture once things settle.
VIDEO_MODES = ["auto", "half", "quarter", "off"]
BUSY_FRACTION = 0.3  # a full-size frame this large (vs. the framebuffer) is not sent: go small
CALM_FRACTION = 0.15  # a small frame below this is "calm"
CALM_TIME = 0.4  # seconds of calm before the sharp picture is sent
SLOW_AFTER = 1.0  # seconds at half size before judging the frame rate
REMEMBER = 20.0  # seconds a quarter-size verdict is remembered across pauses
TICK = 1 / 60

_HELLO, _ACK = "hello", "ack"


class Session:
    def __init__(self, port, renderer, injector, mapper, origin=(0, 0),
                 fps=20, dither=False, colors="256", lz4=True, video="auto", stats=0,
                 video_fps=8, log=print):
        self.port = port  # an open serial.Serial (or anything with read/write)
        self.renderer = renderer
        self.injector = injector
        self.mapper = mapper
        self.origin = origin  # monitor's top-left corner in pointer coordinates
        self.frame_interval = 1.0 / fps
        self.dither = dither
        self.lz4 = lz4
        self.log = log
        self.video = "half" if video == "on" else video
        self.video_fps = video_fps  # below this at half size, automatic mode goes to quarter size
        self.stats_interval = stats  # seconds between throughput reports; 0 = none
        self.mode = MODES[colors]  # the format currently on the wire (may be a half-res one)
        self.shown = None  # the calculator's framebuffer as we believe it to be
        self._shown_mapping = None  # which part of the desktop that picture shows
        self.frames = 0
        self.bytes_sent = 0
        self._wanted_mode = self.mode  # the colour mode the user chose
        self._level = 0  # automatic video mode: 0 = full size, 1 = half, 2 = quarter
        self._calm_since = 0.0
        self._busy_since = 0.0
        self._recent = collections.deque()  # when the last second's frames were sent
        self._remembered = (1, -REMEMBER)  # (level, when) automatic mode last left video mode
        self._announce = True  # the next frame must be preceded by the mode
        self._streaming = False  # a HELLO has been answered
        self._cursor_sent = None
        self._events = queue.Queue()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._error = None
        self._warned_version = False
        self.calc_version = PROTO_VERSION
        mapper.on_colors = self.cycle_colors
        mapper.on_video = self.cycle_video

    def stop(self):
        self._stop.set()

    def cycle_colors(self):
        """Switch to the next colour mode (applied with the next frame)."""
        names = MODE_ORDER
        self._wanted_mode = MODES[names[(names.index(self._wanted_mode.name) + 1) % len(names)]]

    def cycle_video(self):
        """auto -> always half size -> always quarter size -> never."""
        self.video = VIDEO_MODES[(VIDEO_MODES.index(self.video) + 1) % len(VIDEO_MODES)]

    def _target_mode(self, now):
        """The format the next frame should be in."""
        if self.video == "auto":
            if self._level and now - self._calm_since > CALM_TIME:
                self._remembered = (self._level, now)
                self._level = 0
            shift = self._level
        else:
            shift = {"half": 1, "quarter": 2}.get(self.video, 0)
        # Quarter size needs a calculator program that knows about it.
        return scaled_mode(self._wanted_mode, min(shift, 2 if self.calc_version >= 6 else 1))

    def _go_small(self, now):
        """Automatic mode: the picture is changing too much for full size."""
        level, when = self._remembered
        self._level = level if now - when < REMEMBER else 1
        self._calm_since = self._busy_since = now
        self._recent.clear()

    def _watch_frame_rate(self, now):
        """Automatic mode, after a small frame was sent: if half size still
        cannot keep up on this link, go to quarter size."""
        self._recent.append(now)
        while now - self._recent[0] > 1.0:
            self._recent.popleft()
        still_busy = now - self._calm_since < 0.25
        if (self._level == 1 and still_busy and now - self._busy_since > SLOW_AFTER
                and len(self._recent) < self.video_fps):
            self._level = 2

    # -- calculator -> host -----------------------------------------------

    def _read_exact(self, count):
        data = b""
        while len(data) < count and not self._stop.is_set():
            data += self.port.read(count - len(data))
        return data

    def _reader(self):
        try:
            while not self._stop.is_set():
                head = self.port.read(1)
                if not head:
                    continue
                kind = head[0]
                if kind == MSG_HELLO:
                    version = self._read_exact(1)
                    if version and version[0] in COMPATIBLE_VERSIONS:
                        self.calc_version = version[0]
                        self._events.put(_HELLO)
                    elif version and not self._warned_version:
                        self._warned_version = True
                        self.log("The calculator runs protocol version %d but this host speaks "
                                 "version %d. Install the matching REMOTE.8xp."
                                 % (version[0], PROTO_VERSION))
                elif kind == MSG_ACK:
                    self._events.put(_ACK)
                elif kind == MSG_KEYS:
                    matrix = self._read_exact(KEY_GROUPS)
                    if len(matrix) == KEY_GROUPS:
                        self.mapper.update(matrix)
                elif kind == MSG_BYE:
                    self.log("Calculator client exited.")
                    self.mapper.release_all()
        except (serial.SerialException, OSError) as exc:
            self._error = exc
        finally:
            self._stop.set()

    def _ticker(self):
        while not self._stop.wait(TICK):
            self.mapper.scale = self.renderer.viewport.scale
            try:
                self.mapper.tick()
            except Exception as exc:  # an input backend failure should not kill the stream
                self.log("Input error: %s" % exc)
            # Keep the calculator's pointer in step, unless a frame is being written.
            if self._streaming and self._write_lock.acquire(blocking=False):
                try:
                    self._send_cursor()
                except (serial.SerialException, OSError) as exc:
                    self._error = exc
                    self._stop.set()
                except Exception as exc:  # e.g. the pointer position could not be read
                    self.log("Pointer update failed: %s" % exc)
                finally:
                    self._write_lock.release()

    # -- host -> calculator -----------------------------------------------

    def _write(self, messages):
        """Send host messages. Caller holds the write lock.

        Goes out packet by packet, and the pointer position is slipped in
        between them whenever it has changed, so the pointer keeps up even
        while a large frame is on its way.
        """
        for packet in packets(messages, self.lz4):
            self._send_cursor()
            self.port.write(packet)
            self.bytes_sent += len(packet)

    def _pointer(self):
        px, py = self.injector.position()
        return px - self.origin[0], py - self.origin[1]

    def _send_cursor(self, force=False):
        """Tell the calculator where to draw the pointer. Caller holds the write lock."""
        spot = self.renderer.to_lcd(self._pointer()) or (0, CURSOR_HIDDEN)
        if force or spot != self._cursor_sent:
            packet = cursor_packet(*spot)
            self.port.write(packet)
            self.bytes_sent += len(packet)
            self._cursor_sent = spot

    def _render(self):
        viewport = self.renderer.viewport
        byte_step = max(1, 8 // self.mode.bpp)  # pans along framebuffer rows go in whole bytes
        sideways = viewport.rotation in (90, 270)
        viewport.x_step, viewport.y_step = (1, byte_step) if sideways else (byte_step, 1)
        status = "%s  %s  video:%s" % (self.mapper.status(), self.mode.label, self.video)
        # A small picture does not need the slow, sharp downscaling filter.
        rgb = self.renderer.render(self._pointer(), status, fast=self.mode.half)
        return self.mode.pack(rgb, self.dither)

    def _pan(self, mapping):
        """If the view slid since the last frame sent, the move that follows it."""
        old = self._shown_mapping
        if self.mode.half or old is None or mapping is None or old[2:] != mapping[2:]:
            return None
        shift_x = (old[0] - mapping[0]) / mapping[2]
        shift_y = (old[1] - mapping[1]) / mapping[3]
        if not (shift_x.is_integer() and shift_y.is_integer()):
            return None
        turned = self.renderer.turn_vector(int(shift_x), int(shift_y))
        return pan_move(*turned, self.renderer.picture_rect(mapping), self.mode.bpp)

    def _encode(self, frame, mapping=None):
        """Frame message taking the calculator from self.shown to `frame`."""
        if self.shown is None:
            return encode_frame(frame)
        best = encode_frame(frame, self.shown)
        stride = self.mode.stride
        new2d, old2d = frame.reshape(-1, stride), self.shown.reshape(-1, stride)
        # Try shifting what is already on the calculator: the whole picture if
        # the view panned, or a block of rows if something scrolled.
        for move in (self._pan(mapping), find_scroll(new2d, old2d)):
            if move and move_is_valid(old2d.shape[0], stride, *move):
                moved = self.shown.copy()
                apply_move(moved.reshape(-1, stride), *move)
                candidate = encode_frame(frame, moved, move_op(*move))
                if len(candidate) < len(best):
                    best = candidate
        return best

    def run(self):
        """Serve until the calculator disconnects or stop() is called."""
        threads = [threading.Thread(target=self._reader, daemon=True),
                   threading.Thread(target=self._ticker, daemon=True)]
        for thread in threads:
            thread.start()

        credits = 0
        last_sent = 0.0
        next_render = 0.0
        report_at = time.monotonic() + self.stats_interval
        report_frames, report_bytes = 0, 0
        try:
            while not self._stop.is_set():
                # Wait for the calculator, or for the next frame slot.
                if self._streaming and credits > 0:
                    wait = max(0.0, next_render - time.monotonic())
                else:
                    wait = 0.2
                try:
                    event = self._events.get(timeout=wait) if wait else self._events.get_nowait()
                except queue.Empty:
                    event = None
                if event == _HELLO:
                    if not self._streaming:
                        self.log("Calculator connected, streaming.")
                    self._streaming = True
                    self.shown = None
                    self._announce = True
                    credits = FRAME_WINDOW
                    continue
                if event == _ACK:
                    credits = min(FRAME_WINDOW, credits + 1)
                    continue
                if not self._streaming or credits <= 0:
                    continue
                if time.monotonic() < next_render:
                    continue
                next_render = time.monotonic() + self.frame_interval

                target = self._target_mode(time.monotonic())
                if (target.name, target.shift) != (self.mode.name, self.mode.shift):
                    old = self.mode
                    # Back to full size: the calculator still shows the enlarged
                    # small picture, so the sharp one can be sent as changes to it.
                    refine = (self.calc_version >= 6 and old.shift and not target.shift
                              and old.name == target.name and self.shown is not None)
                    self.shown = old.enlarged(self.shown) if refine else None
                    self._shown_mapping = None
                    self.mode = target
                    self._announce = True

                frame = self._render()
                mapping = self.renderer.mapping
                fresh = self.shown is None
                if (not fresh and not self._announce and np.array_equal(frame, self.shown)
                        and time.monotonic() - last_sent < KEEPALIVE):
                    continue

                body = self._encode(frame, mapping)
                now = time.monotonic()
                if self.video == "auto":
                    if self.mode.shift:
                        if fresh or len(body) > CALM_FRACTION * self.mode.fb_size:
                            self._calm_since = now
                    elif not self._announce and len(body) > BUSY_FRACTION * self.mode.fb_size:
                        # Too much changed to send at full size without a long
                        # wait: show it small right away instead.
                        self._go_small(now)
                        next_render = now
                        continue

                out = b""
                announced = self._announce
                if announced:
                    # Older calculator programs do not know how to turn the pointer,
                    # or about picture sizes other than half.
                    turns = self.renderer.viewport.rotation // 90 if self.calc_version >= 5 else 0
                    scale = self.mode.scale_message() if self.calc_version >= 6 else b""
                    out = scale + self.mode.mode_message(turns) + self.mode.palette_message()
                out += body
                try:
                    with self._write_lock:
                        self._write(out)
                        self._send_cursor(force=announced)
                except serial.SerialTimeoutException:
                    # The calculator stopped reading; it will say HELLO again.
                    self._streaming = False
                    continue
                self.shown = frame
                self._shown_mapping = mapping
                self._announce = False
                credits -= 1
                last_sent = time.monotonic()
                self.frames += 1
                if self.video == "auto" and self.mode.shift:
                    self._watch_frame_rate(last_sent)

                if self.stats_interval and last_sent >= report_at:
                    span = self.stats_interval + last_sent - report_at
                    self.log("%.1f fps, %.0f KB/s, %s%s"
                             % ((self.frames - report_frames) / span,
                                (self.bytes_sent - report_bytes) / span / 1024,
                                self.mode.label, ("", " half-size", " quarter-size")[self.mode.shift]))
                    report_at = last_sent + self.stats_interval
                    report_frames, report_bytes = self.frames, self.bytes_sent
        except (serial.SerialException, OSError) as exc:
            self._error = exc
        finally:
            self._stop.set()
            for thread in threads:
                thread.join(timeout=1.0)
            self.mapper.release_all()
        return self._error
