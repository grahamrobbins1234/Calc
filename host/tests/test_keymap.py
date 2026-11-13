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
