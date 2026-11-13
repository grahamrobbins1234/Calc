"""Full host stack against a fake calculator on a pseudo-terminal.

Needs a real X display (CI runs it under xvfb-run); skipped otherwise.
"""

import os
import sys
import threading
import time

import numpy as np
import pytest

from fakecalc import FakeCalc
from tiremote.palette import MODES
from tiremote.protocol import CURSOR_HIDDEN

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"),
    reason="needs Linux with an X display")


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
        calc = FakeCalc(master)
        yield calc, session, injector, viewport, mapper
        calc.close()
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


def in_sync(calc, session):
    """The calculator shows exactly what the host believes it shows."""
    return (calc.error is None and calc.fb is not None and session.shown is not None
            and calc.fb.size == session.shown.size and (calc.fb == session.shown).all())


def test_stream_and_input(stack):
    calc, session, injector, viewport, mapper = stack
    injector._mouse.position = (100, 100)

    # Handshake: mode, palette, a full frame and the pointer position.
    calc.hello()
    assert wait_for(lambda: calc.frames >= 1 and calc.cursor is not None)
    assert calc.bpp == 8 and calc.palette == MODES["256"].palette_message()[2:]
    assert wait_for(lambda: in_sync(calc, session))
    assert calc.cursor == (100 * 320 // 1280, 100 * 180 // 720)

    # A static screen costs next to nothing: only small keep-alive frames.
    time.sleep(0.3)
    before, frames = calc.wire_bytes, calc.frames
    time.sleep(1.2)
    assert 1 <= calc.frames - frames <= 4
    assert calc.wire_bytes - before < 100

    # Arrow keys move the real pointer, and the calculator's pointer follows
    # through cursor messages alone - no picture data.
    before, start_x = calc.wire_bytes, calc.cursor[0]
    calc.keys("right")
    assert wait_for(lambda: injector.position()[0] > 100)
    assert wait_for(lambda: injector.position()[0] > 200), "pointer should glide while held"
    calc.keys()
    time.sleep(0.1)
    x_after = injector.position()[0]
    time.sleep(0.2)
    assert injector.position()[0] == x_after, "pointer must stop on release"
    assert wait_for(lambda: calc.cursor[0] == x_after * 320 // 1280)
    assert calc.cursor[0] > start_x
    assert calc.wire_bytes - before < 1500
    assert in_sync(calc, session)

    # Zoom key switches to a closer view; the calculator's copy stays exact.
    frames = calc.frames
    calc.keys("zoom")
    calc.keys()
    assert wait_for(lambda: viewport.level == 1)
    assert wait_for(lambda: calc.frames > frames + 1 and in_sync(calc, session))

    # [2nd]+[clear] cycles the colour mode: 16 greys, then black and white.
    for bpp in (4, 1):
        calc.keys("2nd")
        calc.keys("2nd", "clear")
        calc.keys()
        assert wait_for(lambda: calc.bpp == bpp and in_sync(calc, session))
        assert calc.fb.size == 320 * 240 * bpp // 8

    # A resync request gets mode, palette and a complete frame again.
    frames = calc.frames
    calc.palette = None
    calc.hello()
    assert wait_for(lambda: calc.frames > frames and calc.palette is not None)
    assert wait_for(lambda: in_sync(calc, session))

    # Pointer leaves the shared view: the calculator is told to hide it.
    viewport.level = len(viewport.scales) - 1
    injector._mouse.position = (20, 20)
    assert wait_for(lambda: calc.cursor is not None and calc.cursor[1] != CURSOR_HIDDEN)
    assert calc.error is None


def test_frame_window_and_version_check(stack):
    calc, session, injector, viewport, mapper = stack
    logged = []
    session.log = logged.append

    calc.hello(version=1)  # an old calculator program
    time.sleep(0.5)
    assert calc.frames == 0 and any("version" in line for line in logged)

    # Without acknowledgements the host stops after the window of two frames.
    calc.auto_ack = False
    calc.hello()
    assert wait_for(lambda: calc.frames == 2)
    time.sleep(1.2)
    assert calc.frames == 2
    calc.ack()
    assert wait_for(lambda: calc.frames == 3)
    time.sleep(0.7)
    assert calc.frames == 3
    assert in_sync(calc, session)


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
    calc.bye()
    assert wait_for(lambda: not mapper._held)
    disp.close()
