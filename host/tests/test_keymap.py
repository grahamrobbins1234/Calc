from tiremote.keymap import KEY_BITS, KeyMapper
from tiremote.view import Viewport


class Recorder:
    def __init__(self):
        self.events = []

    def key(self, name, down):
        self.events.append(("key", name, down))

    def button(self, name, down):
        self.events.append(("button", name, down))

    def scroll(self, dy):
        self.events.append(("scroll", dy))

    def move(self, dx, dy):
        self.events.append(("move", dx, dy))


def matrix(*names):
    data = bytearray(7)
    for name in names:
        group, bit = KEY_BITS[name]
        data[group] |= 1 << bit
    return bytes(data)


def tap(mapper, *names):
    mapper.update(matrix(*names))
    mapper.update(matrix())


def test_key_bits_are_unique():
    assert len(set(KEY_BITS.values())) == len(KEY_BITS) == 49


def test_digits_and_shift():
    rec = Recorder()
    mapper = KeyMapper(rec)
    tap(mapper, "7")
    assert rec.events == [("key", "7", True), ("key", "7", False)]
    rec.events.clear()
    mapper.update(matrix("2nd"))
    mapper.update(matrix("2nd", "1"))
    mapper.update(matrix("2nd"))
    mapper.update(matrix())
    assert rec.events == [("key", "shift", True), ("key", "!", True),
                          ("key", "!", False), ("key", "shift", False)]


def test_alpha_layer_and_ctrl_combo():
    rec = Recorder()
    mapper = KeyMapper(rec)
    tap(mapper, "prgm")
    assert rec.events == [("key", "delete", True), ("key", "delete", False)]
    rec.events.clear()
    tap(mapper, "alpha")
    assert mapper.alpha and rec.events == []
    mapper.update(matrix("vars"))  # Ctrl+C
    mapper.update(matrix("vars", "prgm"))
    mapper.update(matrix("vars"))
    mapper.update(matrix())
    assert rec.events == [("key", "ctrl", True), ("key", "c", True),
                          ("key", "c", False), ("key", "ctrl", False)]
    rec.events.clear()
    # A key held across a layer switch releases what it pressed.
    mapper.update(matrix("math"))
    mapper.update(matrix("math", "alpha"))
    mapper.update(matrix())
    assert rec.events == [("key", "a", True), ("key", "a", False)]
    assert not mapper.alpha


def test_mouse_buttons_scroll_and_zoom():
    rec = Recorder()
    zooms = []
    mapper = KeyMapper(rec, on_zoom=zooms.append)
    tap(mapper, "y=")
    tap(mapper, "window")
    assert rec.events == [("button", "left", True), ("button", "left", False),
                          ("button", "right", True), ("button", "right", False)]
    rec.events.clear()
    mapper.update(matrix("trace"), now=10.0)
    mapper.tick(now=10.1)
    mapper.tick(now=10.31)
    mapper.tick(now=10.40)
    mapper.update(matrix(), now=10.5)
    mapper.tick(now=10.6)
    assert rec.events == [("scroll", 1)] * 3
    tap(mapper, "zoom")
    mapper.update(matrix("2nd"))
    mapper.update(matrix("2nd", "zoom"))
    mapper.update(matrix())
    assert zooms == [1, -1]


def test_second_layer_colour_cycle():
    rec = Recorder()
    mapper = KeyMapper(rec)
    cycles = []
    mapper.on_colors = lambda: cycles.append(1)
    tap(mapper, "clear")
    assert rec.events == [("key", "esc", True), ("key", "esc", False)] and not cycles
    mapper.update(matrix("2nd"))
    mapper.update(matrix("2nd", "clear"))
    mapper.update(matrix())
    assert cycles == [1]
    assert ("key", "esc", True) not in rec.events[2:]


def test_arrows_move_then_glide():
    rec = Recorder()
    mapper = KeyMapper(rec)
    mapper.scale = 2.0
    mapper.update(matrix("right", "down"), now=0.0)
    assert sorted(rec.events) == [("move", 0, 2), ("move", 2, 0)]
    rec.events.clear()
    mapper.tick(now=0.05)
    mapper.tick(now=0.10)
    assert rec.events == []  # still within the tap delay
    for step in range(1, 61):
        mapper.tick(now=0.2 + step / 60)
    moved_x = sum(e[1] for e in rec.events)
    moved_y = sum(e[2] for e in rec.events)
    assert moved_x == moved_y and 150 < moved_x < 600
    mapper.update(matrix(), now=2.0)
    rec.events.clear()
    mapper.tick(now=2.1)
    assert rec.events == []


def test_arrow_key_mode_and_quit_combo():
    rec = Recorder()
    mapper = KeyMapper(rec)
    tap(mapper, "mode")
    assert mapper.arrows_are_keys
    tap(mapper, "up")
    assert rec.events == [("key", "up", True), ("key", "up", False)]
    mapper.update(matrix("2nd"))
    mapper.update(matrix("2nd", "mode"))  # quit combo must not toggle
    assert mapper.arrows_are_keys
    mapper.release_all()
    assert rec.events[-1] == ("key", "shift", False)


def test_viewport_fit_and_follow():
    view = Viewport(1920, 1080)
    assert view.scales == [6.0, 4.0, 2.0, 1.0]
    region, out = view.layout((960, 540))
    assert region == (0, 0, 1920, 1080) and out == (320, 180)
    view.zoom(+5)
    assert view.scale == 1.0
    region, out = view.layout((1900, 1070))
    assert out == (320, 240) and region == (1600, 840, 320, 240)
    x, y, w, h = view.layout((1000, 500))[0]
    assert x <= 1000 - 64 and x + w >= 1000 + 64 and y <= 500 - 48 and y + h >= 500 + 48
    view.zoom(-9)
    assert view.level == 0
    small = Viewport(320, 240)
    assert small.scales == [1.0]


def test_rotated_arrows():
    for rotation, physical, expected in [(0, "up", (0, -1)), (90, "right", (0, -1)),
                                         (90, "down", (1, 0)), (180, "down", (0, -1)),
                                         (270, "left", (0, -1)), (270, "up", (1, 0))]:
        rec = Recorder()
        mapper = KeyMapper(rec)
        mapper.rotation = rotation
        mapper.update(matrix(physical), now=0.0)
        mapper.update(matrix(), now=0.01)
        assert rec.events == [("move",) + expected], (rotation, physical)
    # In arrow-key mode the turned key presses the turned arrow, and releases it.
    rec = Recorder()
    mapper = KeyMapper(rec)
    mapper.rotation = 90
    tap(mapper, "mode")
    tap(mapper, "right")
    assert rec.events == [("key", "up", True), ("key", "up", False)]


def test_rotated_picture_and_pointer_agree():
    import numpy as np
    from PIL import Image

    from tiremote.view import Renderer

    class Desk:
        left = top = 0
        width, height = 480, 640  # a portrait desktop, 2 px per calculator pixel sideways

        def __init__(self):
            self.image = Image.new("RGB", (self.width, self.height), (10, 20, 30))
            self.marks = {(100, 50): (255, 0, 0), (400, 600): (0, 255, 0), (30, 500): (0, 0, 255)}
            for (x, y), colour in self.marks.items():  # 16x16 blocks around each spot
                self.image.paste(colour, (x - 8, y - 8, x + 8, y + 8))

        def grab(self, x, y, w, h):
            return self.image.crop((x, y, x + w, y + h))

    desk = Desk()
    for rotation in (0, 90, 180, 270):
        view = Viewport(desk.width, desk.height, rotation)
        renderer = Renderer(desk, view, "nearest")
        frame = renderer.render((240, 320))
        assert frame.shape == (240, 320, 3)
        if rotation in (90, 270):
            assert view.scales[0] == 2.0  # the portrait desktop fills the sideways screen
            assert (frame != 0).any(axis=2).all()
        for spot, colour in desk.marks.items():
            x, y = renderer.to_lcd(spot)
            assert frame[y, x].tolist() == list(colour), (rotation, spot)
        # Moving right on the desktop moves the way turn_vector says on the calculator.
        ax, ay = renderer.to_lcd((100, 100))
        bx, by = renderer.to_lcd((100 + 2 * view.scale, 100))
        assert (bx - ax, by - ay) == tuple(2 * v for v in renderer.turn_vector(1, 0))


def test_pointer_stays_on_the_shared_monitor():
    from tiremote.inject import NullInjector

    injector = NullInjector((100, 100), bounds=(0, 0, 956, 1470))
    mapper = KeyMapper(injector)
    mapper.scale = 4.0
    for key, edge in (("left", (0, 100)), ("up", (0, 0)), ("right", (955, 0)),
                      ("down", (955, 1469))):
        mapper.update(matrix(key), now=0.0)
        for step in range(1, 1200):
            mapper.tick(now=step / 60)
        mapper.update(matrix(), now=30.0)
        mapper._last_tick = None
        assert injector.position() == edge
    assert NullInjector((5, 5)).bounds is None  # no bounds: free movement, as before
