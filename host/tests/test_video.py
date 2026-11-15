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


LOG = []


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
                      mapper, fps=60, video=video, stats=0.3, log=LOG.append)
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
        assert calc.shift == 0 and calc.fb.size == 76800

        # The picture starts changing everywhere: the very next frame is small.
        frames, sent = calc.frames, calc.wire_bytes
        screen.playing = True
        assert wait_for(lambda: calc.shift == 1)
        assert calc.wire_bytes - sent < 30000, "no full-size frame of the busy picture was sent"
        assert calc.fb.size == 19200 and calc.bpp == 8
        frames, sent = calc.frames, calc.wire_bytes
        time.sleep(0.8)
        assert calc.shift == 1, "must stay in video mode while the picture keeps changing"
        per_frame = (calc.wire_bytes - sent) / max(1, calc.frames - frames)
        assert calc.frames - frames >= 5 and per_frame < 19200 + 300

        # It settles: full detail comes back, as changes to the enlarged picture.
        screen.playing = False
        assert wait_for(lambda: calc.shift == 0 and in_sync(calc, session))
        assert calc.fb.size == 76800
        changes = calc.mode_changes
        time.sleep(1.0)
        assert calc.mode_changes == changes, "a still picture must not flip modes"
        reports = [line for line in LOG if "fps" in line]
        assert any("half-size" in line for line in reports)
        assert calc.error is None
    finally:
        finish()


def test_slow_link_goes_to_quarter_size():
    # A calculator that draws only ~5 frames a second: half size cannot keep up.
    screen, session, calc, finish = start("auto")
    try:
        calc.auto_ack = False
        calc.hello()
        stop = threading.Event()

        def slow_acks():
            seen = 0
            while not stop.is_set():
                if calc.frames > seen:
                    time.sleep(0.2)
                    calc.ack()
                    seen += 1
                else:
                    time.sleep(0.01)

        threading.Thread(target=slow_acks, daemon=True).start()
        assert wait_for(lambda: calc.frames >= 1)
        screen.playing = True
        assert wait_for(lambda: calc.shift == 1)
        assert wait_for(lambda: calc.shift == 2, timeout=6), "should step down to quarter size"
        assert calc.fb.size == 4800
        # After a short pause it goes straight back to quarter size, not via half.
        screen.playing = False
        assert wait_for(lambda: calc.shift == 0, timeout=6)
        seen_half = []
        screen.playing = True
        assert wait_for(lambda: seen_half.append(calc.shift) or calc.shift == 2, timeout=6)
        assert 1 not in seen_half
        stop.set()
        assert calc.error is None
    finally:
        finish()


def test_older_calculator_program_gets_half_size_and_full_refresh():
    screen, session, calc, finish = start("quarter")
    try:
        calc.hello(version=5)
        assert wait_for(lambda: calc.shift == 1 and in_sync(calc, session))
        calc.keys("2nd")  # quarter -> off
        calc.keys("2nd", "del")
        calc.keys()
        assert wait_for(lambda: calc.shift == 0 and in_sync(calc, session))
        assert calc.error is None
    finally:
        finish()


def test_video_mode_can_be_forced_and_cycled():
    screen, session, calc, finish = start("half")
    try:
        calc.hello()
        assert wait_for(lambda: calc.shift == 1 and in_sync(calc, session))

        def press_video_key():
            calc.keys("2nd")  # [2nd]+[del]
            calc.keys("2nd", "del")
            calc.keys()

        press_video_key()  # half -> quarter
        assert wait_for(lambda: session.video == "quarter" and calc.shift == 2
                        and in_sync(calc, session))
        press_video_key()  # quarter -> off
        assert wait_for(lambda: session.video == "off" and calc.shift == 0
                        and in_sync(calc, session))
        screen.playing = True
        time.sleep(0.8)
        assert calc.shift == 0, "'off' never drops resolution"

        press_video_key()  # off -> auto, which now reacts to the busy picture
        assert wait_for(lambda: session.video == "auto" and calc.shift >= 1)

        # Colour mode changes carry over into video mode.
        calc.keys("2nd")
        calc.keys("2nd", "clear")
        calc.keys()
        assert wait_for(lambda: calc.shift >= 1 and calc.bpp == 4)
        assert calc.error is None
    finally:
        finish()


def _flat_picture(colours):
    """A 640x480 picture of vertical bands in the given colours."""
    bands = np.repeat(np.array(colours, dtype=np.uint8)[None, :, :], 480, axis=0)
    return Image.fromarray(np.repeat(bands, 640 // len(colours), axis=1))


def test_adaptive_palette_follows_the_scene():
    """16a: when the picture's colours change, the calculator gets a new
    palette and a full refresh, and stays exact."""
    from tiremote.palette import MODES

    screen, session, calc, finish = start("off")
    try:
        day = [(90, 170, 250), (70, 140, 100), (95, 110, 130), (250, 10, 10),
               (255, 255, 255), (10, 10, 10), (250, 140, 0), (240, 240, 20)]
        screen._still = _flat_picture(day)
        session._wanted_mode = MODES["16a"]
        calc.hello()
        assert wait_for(lambda: calc.bpp == 4 and in_sync(calc, session))
        first_palette = calc.palette
        assert len(first_palette) == 32

        def shows(colours):
            if not in_sync(calc, session):
                return False
            picture = session.mode.to_rgb(calc.fb).astype(int)
            return all(np.abs(picture[100, 20 + 40 * i] - colour).max() < 12
                       for i, colour in enumerate(colours))

        assert wait_for(lambda: shows(day))

        # A scene in completely different colours: new ones are chosen.
        night = [(20, 10, 60), (130, 60, 160), (60, 200, 190), (200, 180, 120),
                 (40, 80, 30), (160, 20, 90), (100, 100, 220), (230, 120, 170)]
        screen._still = _flat_picture(night)
        assert wait_for(lambda: calc.palette != first_palette and shows(night), timeout=6)
        changes = calc.mode_changes
        time.sleep(1.0)
        assert calc.mode_changes == changes, "a still picture must keep its palette"
        assert calc.error is None
    finally:
        finish()
