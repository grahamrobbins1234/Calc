"""A Python stand-in for the calculator program, speaking protocol v2 on a file descriptor."""

import os
import select
import struct
import threading

import numpy as np

from tiremote.encoder import apply_move, unpacketize
from tiremote.protocol import (LCD_H, LCD_W, MSG_ACK, MSG_BYE, MSG_FRAME, MSG_HELLO,
                               MSG_KEYS, MSG_MODE, MSG_PALETTE, OP_END, OP_LIT, OP_MOVE, OP_RUN,
                               PROTO_VERSION)


class _Stopped(Exception):
    pass


class FakeCalc:
    def __init__(self, fd, auto_ack=True):
        self.fd = fd
        self.auto_ack = auto_ack
        self.bpp = None
        self.half = False
        self.mode_changes = 0
        self.fb = None
        self.palette = None
        self.cursor = None
        self.frames = 0
        self.moves = 0
        self.wire_bytes = 0
        self.error = None
        self._plain = b""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # -- sending ------------------------------------------------------------

    def send(self, *data):
        os.write(self.fd, bytes(data))

    def hello(self, version=PROTO_VERSION):
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
            self._plain += unpacketize(self._raw, self._on_cursor)
        data, self._plain = self._plain[:count], self._plain[count:]
        return data

    def _on_cursor(self, spot):
        self.cursor = spot

    def _frame(self):
        width = LCD_W // 2 if self.half else LCD_W
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
                    self.bpp, self.half = mode & 0x7F, bool(mode & 0x80)
                    assert self.bpp in (8, 4, 1)
                    self.mode_changes += 1
                    # Real video memory holds leftovers here, so start from noise.
                    self.fb = np.random.default_rng(0).integers(
                        0, 256, LCD_W * LCD_H * self.bpp // 8 // (4 if self.half else 1),
                        dtype=np.uint8)
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
