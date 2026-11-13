"""Full host stack against a fake calculator on a pseudo-terminal.

Needs a real X display (CI runs it under xvfb-run); skipped otherwise.
"""

import os
import select
import sys
import threading
import time

import numpy as np
import pytest

from tiremote.palette import palette_1555
from tiremote.protocol import (FB_SIZE, MSG_BYE, MSG_FRAME, MSG_HELLO, MSG_KEYS, MSG_PALETTE,
                               MSG_REQ, OP_END, OP_LIT, OP_RUN, PROTO_VERSION)
from test_keymap import matrix

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"),
    reason="needs Linux with an X display")


class FakeCalc:
    """Speaks the calculator side of the protocol on a pty master."""

    def __init__(self, fd):
        self.fd = fd
        self.fb = np.zeros(FB_SIZE, np.uint8)
        self.palette = None

    def send(self, *data):
        os.write(self.fd, bytes(data))

    def keys(self, *names):
        os.write(self.fd, bytes([MSG_KEYS]) + matrix(*names))

    def _read(self, count):
        data = b""
        while len(data) < count:
            ready, _, _ = select.select([self.fd], [], [], 5.0)
            assert ready, "host went quiet"
            data += os.read(self.fd, count - len(data))
        return data

    def receive_frame(self):
        """Mirror of the calculator's exchange(): returns bytes consumed."""
        used = 0
        while True:
            kind = self._read(1)[0]
            used += 1
            if kind == MSG_PALETTE:
                self.palette = self._read(512)
                used += 512
                continue
            assert kind == MSG_FRAME
            pos = 0
            while True:
                header = self._read(1)[0]
                used += 1
                op = header & 0xC0
                if op == OP_END:
                    return used
                count = header & 0x3F
                if not count:
                    lo, hi = self._read(2)
                    count = lo | hi << 8
                    used += 2
                assert pos + count <= FB_SIZE
                if op == OP_RUN:
                    self.fb[pos:pos + count] = self._read(1)[0]
                    used += 1
                elif op == OP_LIT:
                    self.fb[pos:pos + count] = np.frombuffer(self._read(count), np.uint8)
                    used += count
                pos += count


@pytest.fixture
def stack():
    import serial

    from tiremote.capture import MssGrabber
    from tiremote.inject import PynputInjector
    from tiremote.keymap import KeyMapper
    from tiremote.session import Session
    from tiremote.view import Renderer, Viewport

    master, slave = os.openpty()
    port = serial.Serial(os.ttyname(slave), 115200, timeout=0.2, write_timeout=2.0)
    grabber = MssGrabber(1)
    viewport = Viewport(grabber.width, grabber.height)
    injector = PynputInjector()
    mapper = KeyMapper(injector, on_zoom=viewport.zoom)
    session = Session(port, Renderer(grabber, viewport), injector, mapper,
                      (grabber.left, grabber.top), fps=60, log=lambda *_: None)
    thread = threading.Thread(target=session.run, daemon=True)
    thread.start()
    try:
        yield FakeCalc(master), session, injector, viewport, mapper
    finally:
        session.stop()
        thread.join(timeout=3)
        port.close()
        os.close(master)
        os.close(slave)


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_stream_and_input(stack):
    calc, session, injector, viewport, mapper = stack
    injector._mouse.position = (100, 100)

    # Handshake: palette plus a full frame.
    calc.send(MSG_HELLO, PROTO_VERSION)
    calc.receive_frame()
    assert calc.palette == palette_1555()
    first = calc.fb.copy()
    assert first.reshape(240, 320)[:, :].any(), "frame should contain the cursor/status"

    # A static screen answers with an (almost) empty delta.
    calc.send(MSG_REQ)
    assert calc.receive_frame() < 200

    # Arrow keys move the real pointer; the next delta shows the cursor moving.
    calc.keys("right")
    assert wait_for(lambda: injector.position()[0] > 100)
    assert wait_for(lambda: injector.position()[0] > 200), "pointer should glide while held"
    calc.keys()
    time.sleep(0.1)
    x_after = injector.position()[0]
    time.sleep(0.2)
    assert injector.position()[0] == x_after, "pointer must stop on release"
    calc.send(MSG_REQ)
    calc.receive_frame()
    assert (calc.fb != first).any()

    # Zoom key switches to a 1:1 crop; the calculator's copy stays exact.
    calc.keys("zoom")
    calc.keys()
    assert wait_for(lambda: viewport.level == 1)
    for _ in range(3):
        calc.send(MSG_REQ)
        calc.receive_frame()
    time.sleep(0.2)
    calc.send(MSG_REQ)
    calc.receive_frame()
    expected = session._render()
    assert (calc.fb == expected).all()

    # A resync request gets the palette and a complete frame again.
    calc.fb[:] = 0
    calc.palette = None
    calc.send(MSG_HELLO, PROTO_VERSION)
    calc.receive_frame()
    assert calc.palette is not None and (calc.fb == session._render()).all()


def test_keyboard_reaches_x_server(stack):
    from Xlib import X, display

    calc, session, injector, viewport, mapper = stack

    # A focused X window, to see exactly what an application would receive.
    disp = display.Display()
    screen = disp.screen()
    window = screen.root.create_window(0, 0, 200, 200, 0, screen.root_depth,
                                       event_mask=X.KeyPressMask)
    window.map()
    disp.sync()
    time.sleep(0.2)
    window.set_input_focus(X.RevertToParent, X.CurrentTime)
    disp.sync()

    for names in (["7"], ["alpha"], ["math"], ["enter"], ["2nd"], ["2nd", "sto"]):
        calc.keys(*names)  # 7, alpha lock, 'a', Enter, then Shift + 'x'
        time.sleep(0.05)
        if names != ["2nd"]:
            calc.keys(*names[:-1])
            time.sleep(0.05)
    calc.keys()

    typed = []

    def drain():
        disp.sync()
        while disp.pending_events():
            event = disp.next_event()
            if event.type == X.KeyPress:
                shifted = bool(event.state & X.ShiftMask)
                typed.append(disp.keycode_to_keysym(event.detail, 1 if shifted else 0))
        return len(typed) >= 5

    assert wait_for(drain)
    # '7', 'a', Return, Shift_L, 'X'
    assert typed == [ord("7"), ord("a"), 0xFF0D, 0xFFE1, ord("X")]

    # Held keys are released when the calculator says goodbye.
    calc.keys("y=")
    assert wait_for(lambda: "y=" in mapper._held)
    calc.send(MSG_BYE)
    assert wait_for(lambda: not mapper._held)
    disp.close()
