"""Automatic video mode: half resolution while the picture is busy, full detail when it settles."""

import socket
import threading
import time

import numpy as np
from PIL import Image

from fakecalc import FakeCalc
from tiremote import net
from tiremote.inject import NullInjector
from tiremote.keymap import KeyMapper
from tiremote.session import Session
from tiremote.view import Renderer, Viewport


class GameScreen:
    """A 640x480 desktop that is either still or changing everywhere, like a game."""

    left = top = 0
    width, height = 640, 480

    def __init__(self):
        self.playing = False
        self._rng = np.random.default_rng(0)
        self._still = self._picture()

    def _picture(self):
        blocks = self._rng.integers(0, 256, (60, 80, 3), dtype=np.uint8)
        return Image.fromarray(np.kron(blocks, np.ones((8, 8, 1), dtype=np.uint8)))

    def grab(self, x, y, w, h):
        image = self._picture() if self.playing else self._still
        return image.crop((x, y, x + w, y + h))


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def start(video):
    host_end, calc_end = socket.socketpair()
    screen = GameScreen()
    viewport = Viewport(screen.width, screen.height)
    injector = NullInjector((320, 240))
    mapper = KeyMapper(injector, on_zoom=viewport.zoom)
    session = Session(net.NetPort(host_end), Renderer(screen, viewport, "nearest"), injector,
                      mapper, fps=60, video=video, log=lambda *_: None)
    thread = threading.Thread(target=session.run, daemon=True)
    thread.start()
    calc = FakeCalc(calc_end.fileno())

    def finish():
        session.stop()
        thread.join(timeout=3)
        calc.close()
        host_end.close()
        calc_end.close()

    return screen, session, calc, finish


def in_sync(calc, session):
    return (calc.error is None and calc.fb is not None and session.shown is not None
            and calc.fb.size == session.shown.size and (calc.fb == session.shown).all())


def test_auto_video_mode_follows_activity():
    screen, session, calc, finish = start("auto")
    try:
        calc.hello()
        assert wait_for(lambda: calc.frames >= 1 and in_sync(calc, session))
        assert not calc.half and calc.fb.size == 76800

        # The picture starts changing everywhere: drop to half resolution.
        screen.playing = True
        assert wait_for(lambda: calc.half)
        assert calc.fb.size == 19200 and calc.bpp == 8
        frames, sent = calc.frames, calc.wire_bytes
        time.sleep(1.0)
        assert calc.half, "must stay in video mode while the picture keeps changing"
        per_frame = (calc.wire_bytes - sent) / max(1, calc.frames - frames)
        assert calc.frames - frames >= 5 and per_frame < 19200 + 300

        # It settles: full detail comes back and matches exactly.
        screen.playing = False
        assert wait_for(lambda: not calc.half and in_sync(calc, session))
        assert calc.fb.size == 76800
        changes = calc.mode_changes
        time.sleep(1.0)
        assert calc.mode_changes == changes, "a still picture must not flip modes"
        assert calc.error is None
    finally:
        finish()


def test_video_mode_can_be_forced_and_cycled():
    screen, session, calc, finish = start("on")
    try:
        calc.hello()
        assert wait_for(lambda: calc.half and in_sync(calc, session))

        calc.keys("2nd")  # [2nd]+[del]: on -> off
        calc.keys("2nd", "del")
        calc.keys()
        assert wait_for(lambda: session.video == "off" and not calc.half and in_sync(calc, session))
        screen.playing = True
        time.sleep(0.8)
        assert not calc.half, "'off' never drops resolution"

        calc.keys("2nd")  # off -> auto, which now reacts to the busy picture
        calc.keys("2nd", "del")
        calc.keys()
        assert wait_for(lambda: session.video == "auto" and calc.half)

        # Colour mode changes carry over into video mode.
        calc.keys("2nd")
        calc.keys("2nd", "clear")
        calc.keys()
        assert wait_for(lambda: calc.half and calc.bpp == 4 and calc.fb.size == 9600)
        assert calc.error is None
    finally:
        finish()
