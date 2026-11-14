"""A Python stand-in for the calculator program, speaking protocol v2 on a file descriptor."""

import os
import select
import struct
import threading

import numpy as np

from tiremote.encoder import Unpacker, apply_move
from tiremote.palette import MODES, scaled_mode
from tiremote.protocol import (LCD_H, LCD_W, MSG_ACK, MSG_BYE, MSG_FRAME, MSG_HELLO,
                               MSG_KEYS, MSG_MODE, MSG_PALETTE, MSG_SCALE, OP_END, OP_LIT, OP_MOVE, OP_RUN,
                               PROTO_VERSION)


class _Stopped(Exception):
    pass


class FakeCalc:
    def __init__(self, fd, auto_ack=True):
        self.fd = fd
        self.auto_ack = auto_ack
        self.bpp = None
        self.half = False
        self.turn = 0
        self.shift = 0
        self._next_shift = 1
        self.mode_changes = 0
        self.fb = None
        self.palette = None
        self.cursor = None
        self.frames = 0
        self.moves = 0
        self.wire_bytes = 0
        self.error = None
        self._plain = b""
        self._unpacker = Unpacker(self._raw, self._on_cursor)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # -- sending ------------------------------------------------------------

    def send(self, *data):
        os.write(self.fd, bytes(data))

    def hello(self, version=PROTO_VERSION):
        # Like the real program: forget the old stream, start with an empty history.
        self._unpacker.reset()
        self.send(MSG_HELLO, version)

    def ack(self):
        self.send(MSG_ACK)

    def bye(self):
        self.send(MSG_BYE)

    def keys(self, *names):
        from test_keymap import matrix

        os.write(self.fd, bytes([MSG_KEYS]) + matrix(*names))

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)

    # -- receiving ----------------------------------------------------------

    def _raw(self, count):
        data = b""
        while len(data) < count:
            if self._stop.is_set():
                raise _Stopped
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if ready:
                part = os.read(self.fd, count - len(data))
                if not part:
                    raise _Stopped
                data += part
        self.wire_bytes += count
        return data

    def _get(self, count):
        while len(self._plain) < count:
            self._plain += self._unpacker.payload()
        data, self._plain = self._plain[:count], self._plain[count:]
        return data

    def _on_cursor(self, spot):
        self.cursor = spot

    def _frame(self):
        width = LCD_W >> self.shift
        stride = width * self.bpp // 8
        pos = 0
        while True:
            header = self._get(1)[0]
            if header == OP_END:
                return
            if header == OP_MOVE:
                apply_move(self.fb.reshape(-1, stride), *struct.unpack("<HBHBbb", self._get(8)))
                self.moves += 1
                continue
            op, count = header & 0xC0, header & 0x3F
            assert op != 0xC0
            if not count:
                (count,) = struct.unpack("<H", self._get(2))
            assert pos + count <= self.fb.size
            if op == OP_RUN:
                self.fb[pos:pos + count] = self._get(1)[0]
            elif op == OP_LIT:
                self.fb[pos:pos + count] = np.frombuffer(self._get(count), np.uint8)
            pos += count

    def _run(self):
        try:
            while True:
                kind = self._get(1)[0]
                if kind == MSG_MODE:
                    mode = self._get(1)[0]
                    old = (self.bpp, self.shift)
                    self.bpp, self.half = mode & 0x1F, bool(mode & 0x80)
                    self.shift = self._next_shift if self.half else 0
                    self.turn = mode >> 5 & 3
                    assert self.bpp in (8, 4, 1)
                    self.mode_changes += 1
                    size = (LCD_W >> self.shift) * (LCD_H >> self.shift) * self.bpp // 8
                    if old[0] == self.bpp and old[1] and not self.shift:
                        # Same depth, back to full size: the screen keeps the enlarged picture.
                        mode_cls = {8: MODES["256"], 4: MODES["16"], 1: MODES["2"]}[self.bpp]
                        small = scaled_mode(mode_cls, old[1])
                        self.fb = small.enlarged(self.fb).copy()
                    else:
                        # Otherwise video memory holds leftovers, so start from noise.
                        self.fb = np.random.default_rng(0).integers(0, 256, size, dtype=np.uint8)
                elif kind == MSG_SCALE:
                    self._next_shift = self._get(1)[0]
                    assert self._next_shift in (1, 2)
                elif kind == MSG_PALETTE:
                    count = self._get(1)[0] or 256
                    self.palette = self._get(count * 2)
                elif kind == MSG_FRAME:
                    self._frame()
                    self.frames += 1
                    if self.auto_ack:
                        self.ack()
                else:
                    raise AssertionError("unknown message %r" % kind)
        except _Stopped:
            pass
        except Exception as exc:  # surfaced by tests through .error
            self.error = exc
