"""One connected calculator: streams frames and applies its key presses.

Flow control is a small window: the host may have FRAME_WINDOW frames sent
but not yet acknowledged, so one frame can be on the wire while the
calculator draws the previous one. Pointer positions are tiny separate
messages sent as the pointer moves, independent of frames.
"""

import queue
import struct
import threading
import time

import numpy as np
import serial

from .encoder import apply_move, encode_frame, move_op, packetize
from .palette import MODE_ORDER, MODES
from .protocol import (CURSOR_HIDDEN, FRAME_WINDOW, KEY_GROUPS, MSG_ACK, MSG_BYE, MSG_CURSOR,
                       MSG_HELLO, MSG_KEYS, MSG_MODE, PROTO_VERSION)
from .scroll import find_scroll

KEEPALIVE = 0.5  # send something at least this often so the calculator knows we are here
TICK = 1 / 60

_HELLO, _ACK = "hello", "ack"


class Session:
    def __init__(self, port, renderer, injector, mapper, origin=(0, 0),
                 fps=20, dither=False, colors="256", lz4=True, log=print):
        self.port = port  # an open serial.Serial (or anything with read/write)
        self.renderer = renderer
        self.injector = injector
        self.mapper = mapper
        self.origin = origin  # monitor's top-left corner in pointer coordinates
        self.frame_interval = 1.0 / fps
        self.dither = dither
        self.lz4 = lz4
        self.log = log
        self.mode = MODES[colors]
        self.shown = None  # the calculator's framebuffer as we believe it to be
        self.frames = 0
        self.bytes_sent = 0
        self._wanted_mode = self.mode
        self._streaming = False  # a HELLO has been answered
        self._cursor_sent = None
        self._events = queue.Queue()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._error = None
        self._warned_version = False
        mapper.on_colors = self.cycle_colors

    def stop(self):
        self._stop.set()

    def cycle_colors(self):
        """Switch to the next colour mode (applied with the next frame)."""
        names = MODE_ORDER
        self._wanted_mode = MODES[names[(names.index(self._wanted_mode.name) + 1) % len(names)]]

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
                    if version == bytes([PROTO_VERSION]):
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
                finally:
                    self._write_lock.release()

    # -- host -> calculator -----------------------------------------------

    def _write(self, messages):
        """Send host messages. Caller holds the write lock."""
        data = packetize(messages, self.lz4)
        self.port.write(data)
        self.bytes_sent += len(data)

    def _pointer(self):
        px, py = self.injector.position()
        return px - self.origin[0], py - self.origin[1]

    def _send_cursor(self, force=False):
        """Tell the calculator where to draw the pointer. Caller holds the write lock."""
        spot = self.renderer.to_lcd(self._pointer()) or (0, CURSOR_HIDDEN)
        if force or spot != self._cursor_sent:
            self._write(struct.pack("<BHB", MSG_CURSOR, *spot))
            self._cursor_sent = spot

    def _render(self):
        status = "%s  %sc" % (self.mapper.status(), self.mode.name)
        rgb = self.renderer.render(self._pointer(), status)
        return self.mode.pack(rgb, self.dither)

    def _encode(self, frame):
        """Frame message taking the calculator from self.shown to `frame`."""
        if self.shown is None:
            return encode_frame(frame)
        plain = encode_frame(frame, self.shown)
        stride = self.mode.stride
        move = find_scroll(frame.reshape(-1, stride), self.shown.reshape(-1, stride))
        if move:
            moved = self.shown.copy()
            apply_move(moved.reshape(-1, stride), *move)
            scrolled = encode_frame(frame, moved, move_op(*move))
            if len(scrolled) < len(plain):
                return scrolled
        return plain

    def run(self):
        """Serve until the calculator disconnects or stop() is called."""
        threads = [threading.Thread(target=self._reader, daemon=True),
                   threading.Thread(target=self._ticker, daemon=True)]
        for thread in threads:
            thread.start()

        credits = 0
        last_sent = 0.0
        next_render = 0.0
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

                if self._wanted_mode is not self.mode:
                    self.mode = self._wanted_mode
                    self.shown = None

                frame = self._render()
                fresh = self.shown is None
                if (not fresh and np.array_equal(frame, self.shown)
                        and time.monotonic() - last_sent < KEEPALIVE):
                    continue

                out = b""
                if fresh:
                    out = bytes([MSG_MODE, self.mode.bpp]) + self.mode.palette_message()
                out += self._encode(frame)
                try:
                    with self._write_lock:
                        self._write(out)
                        self._send_cursor(force=fresh)
                except serial.SerialTimeoutException:
                    # The calculator stopped reading; it will say HELLO again.
                    self._streaming = False
                    continue
                self.shown = frame
                credits -= 1
                last_sent = time.monotonic()
                self.frames += 1
        except (serial.SerialException, OSError) as exc:
            self._error = exc
        finally:
            self._stop.set()
            for thread in threads:
                thread.join(timeout=1.0)
            self.mapper.release_all()
        return self._error
