import io
import os
import shutil
import struct
import subprocess

import numpy as np
import pytest

from tiremote.encoder import (apply_move, cursor_packet, decode_frame, encode_frame, move_op,
                              packetize, packets, unpacketize)
from tiremote.palette import MODES, half_mode, palette_rgb, quantize
from tiremote.protocol import (CURSOR_HIDDEN, MSG_FRAME, MSG_MODE, OP_END, PKT_LZ4,
                               PKT_MAX)
from tiremote.scroll import find_scroll

HERE = os.path.dirname(__file__)
CALC_SRC = os.path.join(HERE, "..", "..", "calc", "src")
FB_SIZE = 320 * 240


def roundtrip(new, old, stride=320):
    data = encode_frame(new, old)
    fb = np.zeros(new.size, np.uint8) if old is None else old.copy()
    decode_frame(data, fb, stride)
    assert (fb == new).all()
    return data


def random_frames(seed, count, size=FB_SIZE):
    rng = np.random.default_rng(seed)
    frame = rng.integers(0, 4, size, dtype=np.uint8)
    for _ in range(count):
        frame = frame.copy()
        for _ in range(rng.integers(0, 30)):
            start, length = rng.integers(0, size), rng.integers(1, 3000)
            span = frame[start:start + length]
            span[:] = rng.integers(0, 256) if rng.random() < 0.5 else \
                rng.integers(0, 256, span.size)
        yield frame


def page(seed=5):
    """A tall synthetic 'document' with distinct rows, as (rows, 320) uint8."""
    rng = np.random.default_rng(seed)
    doc = np.repeat(rng.integers(0, 256, (600, 40), dtype=np.uint8), 8, axis=1)
    doc[::7] = 255  # some identical blank lines, as real pages have
    return doc


# -- frames -----------------------------------------------------------------


def test_unchanged_frame_is_empty():
    frame = np.arange(FB_SIZE, dtype=np.uint32).astype(np.uint8)
    assert encode_frame(frame, frame) == bytes([MSG_FRAME, OP_END])


def test_solid_and_noise_frames():
    rng = np.random.default_rng(0)
    black = np.zeros(FB_SIZE, np.uint8)
    solid = np.full(FB_SIZE, 200, np.uint8)
    noise = rng.integers(0, 256, FB_SIZE, dtype=np.uint8)
    assert len(roundtrip(black, None)) < 16
    assert len(roundtrip(solid, black)) < 16
    assert len(roundtrip(noise, None)) < FB_SIZE + 64
    roundtrip(noise, solid)
    roundtrip(solid, noise)


def test_edges_and_tiny_changes():
    base = np.zeros(FB_SIZE, np.uint8)
    for index in (0, 1, 62, 63, 64, FB_SIZE // 2 - 1, FB_SIZE // 2, FB_SIZE - 2, FB_SIZE - 1):
        new = base.copy()
        new[index] = 9
        assert len(roundtrip(new, base)) <= 12
    for length in (1, 3, 4, 5, 63, 64, 65, 300, 65535, 65536, FB_SIZE):
        new = base.copy()
        new[:length] = 7
        roundtrip(new, base)
        new[:length] = np.arange(length) % 251
        roundtrip(new, base)


@pytest.mark.parametrize("size,stride", [(76800, 320), (38400, 160), (9600, 40)])
def test_random_deltas_at_every_depth(size, stride):
    previous = None
    for frame in random_frames(size, 60, size):
        roundtrip(frame, previous, stride)
        roundtrip(frame, None, stride)
        previous = frame


# -- scrolling ----------------------------------------------------------------


@pytest.mark.parametrize("dy", [-100, -17, -1, 1, 30, 120])
def test_scroll_is_found_and_cheap(dy):
    doc = page()
    old = doc[200:440].copy()
    new = doc[200 - dy:440 - dy].copy()
    # A static toolbar and sidebar that do not scroll.
    for screen in (old, new):
        screen[:20] = 3
        screen[:, :24] = 9

    move = find_scroll(new, old)
    assert move is not None
    xb, y, wb, h, found_dx, found_dy = move
    assert found_dy == dy and found_dx == 0 and xb == 24 and wb == 296
    assert h == 220 - abs(dy)

    moved = old.copy()
    apply_move(moved, *move)
    message = encode_frame(new.ravel(), moved.ravel(), move_op(*move))
    plain = encode_frame(new.ravel(), old.ravel())
    assert len(message) < len(plain) / 2 or abs(dy) > 100

    fb = old.ravel().copy()
    decode_frame(message, fb)
    assert (fb == new.ravel()).all()


def test_no_scroll_in_unrelated_or_static_frames():
    rng = np.random.default_rng(1)
    a = rng.integers(0, 256, (240, 320), dtype=np.uint8)
    b = rng.integers(0, 256, (240, 320), dtype=np.uint8)
    assert find_scroll(b, a) is None
    assert find_scroll(a, a) is None
    c = a.copy()
    c[100:104, 50:60] = 0  # a small local change
    assert find_scroll(c, a) is None
    blank_old = np.full((240, 320), 255, np.uint8)
    blank_new = blank_old.copy()
    blank_new[50:200] = 0  # big change, but nothing recognisable moved
    assert find_scroll(blank_new, blank_old) is None


def test_moves_in_every_direction(harness):
    """Every shift direction, including overlapping ones, on the calculator's C code."""
    rng = np.random.default_rng(8)
    start = rng.integers(0, 256, (240, 320), dtype=np.uint8)
    setup = bytes([MSG_MODE, 8]) + encode_frame(start.ravel())
    for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0), (-7, -3), (9, -2), (-5, 4), (6, 6),
                   (0, -127), (0, 127), (-127, 0), (127, 0), (-100, 100)]:
        xb, y = max(0, -dx), max(0, -dy)
        move = (xb, y, 320 - abs(dx), 240 - abs(dy), dx, dy)
        expected = start.copy()
        apply_move(expected, *move)
        message = b"F" + move_op(*move) + bytes([OP_END])
        fb = start.ravel().copy()
        decode_frame(message, fb)
        assert (fb == expected.ravel()).all()
        status, _, lifted, _ = run_calc(harness, packetize(setup + message))
        assert status == 0, (dx, dy)
        assert (lifted == expected.ravel()).all(), (dx, dy)


class _Desktop:
    """A fake 1600x1200 desktop of distinct blocks, to pan around on."""

    left = top = 0
    width, height = 1600, 1200

    def __init__(self):
        from PIL import Image

        rng = np.random.default_rng(9)
        blocks = rng.integers(0, 256, (150, 200, 3), dtype=np.uint8)
        self.image = Image.fromarray(np.kron(blocks, np.ones((8, 8, 1), dtype=np.uint8)))

    def grab(self, x, y, w, h):
        return self.image.crop((x, y, x + w, y + h))


@pytest.mark.parametrize("name,scale", [("256", 1), ("256", 2), ("16", 1), ("16", 2), ("2", 1)])
def test_panning_the_view_shifts_instead_of_resending(harness, name, scale):
    """Moving the pointer around a zoomed view: the session's own encoder,
    checked on the calculator's C code."""
    from tiremote.inject import NullInjector
    from tiremote.keymap import KeyMapper
    from tiremote.session import Session
    from tiremote.view import Renderer, Viewport

    grabber = _Desktop()
    viewport = Viewport(grabber.width, grabber.height)
    viewport.level = viewport.scales.index(scale)  # screen pixels per calculator pixel
    injector = NullInjector((800, 600))
    session = Session(None, Renderer(grabber, viewport, "nearest"), injector,
                      KeyMapper(injector), colors=name, log=lambda *_: None)
    mode = session.mode

    wire = b""
    sizes = {}
    walk = [(0, 0)] + [(dx, dy) for dx, dy in
                       [(0, 60), (0, 60), (0, -90), (70, 0), (70, 0), (-120, 0), (48, 48),
                        (-64, 32), (0, 200), (0, -400), (300, 0), (-600, 0), (16, -16)]
                       for _ in range(3)]
    for step, (dx, dy) in enumerate(walk):
        injector.move(dx, dy)
        frame = session._render()
        mapping = session.renderer.mapping
        messages = b""
        if session.shown is None:
            messages = bytes([MSG_MODE, mode.bpp]) + mode.palette_message()
        body = session._encode(frame, mapping)
        if session.shown is not None and not np.array_equal(frame, session.shown):
            sizes[step] = (len(body), len(encode_frame(frame, session.shown)), b"\xc1" in body[:2])
        wire += packetize(messages + body)
        session.shown, session._shown_mapping = frame, mapping

    status, _, lifted, _ = run_calc(harness, wire)
    assert status == 0
    assert (lifted == session.shown).all()

    panned = [v for v in sizes.values() if v[2]]
    assert len(panned) >= len(sizes) * 0.6, "most pans should be sent as shifts"
    assert sum(v[0] for v in panned) < sum(v[1] for v in panned) / 3


# -- packets ------------------------------------------------------------------


def unpack_all(wire):
    stream = io.BytesIO(wire)
    out = b""
    while stream.tell() < len(wire):
        out += unpacketize(stream.read)
    return out


def test_packets_roundtrip_and_compress():
    rng = np.random.default_rng(2)
    text = b"the quick brown fox " * 2000
    noise = bytes(rng.integers(0, 256, 20000, dtype=np.uint8))
    for data in (b"x", text, noise, text + noise, bytes(PKT_MAX), bytes(PKT_MAX + 1)):
        for lz4 in (True, False):
            wire = packetize(data, lz4)
            assert unpack_all(wire) == data
    assert len(packetize(text)) < len(text) / 10
    assert len(packetize(noise)) <= len(noise) + 2 * (len(noise) // PKT_MAX + 1)
    assert not struct.unpack_from("<H", packetize(noise))[0] & PKT_LZ4  # stored, not expanded


# -- the calculator's C code ----------------------------------------------------


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not compiler:
        pytest.skip("no C compiler")
    exe = tmp_path_factory.mktemp("calc") / "harness"
    subprocess.run([compiler, "-O1", "-g", "-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all", "-Wall", "-Wextra", "-Werror", "-I", CALC_SRC,
                    os.path.join(HERE, "decode_harness.c"),
                    os.path.join(CALC_SRC, "decode.c"), "-o", str(exe)], check=True)
    return str(exe)


def run_calc(harness, wire):
    result = subprocess.run([harness], input=wire, capture_output=True)
    if result.returncode != 0:
        return result.returncode, None, None, None
    out = result.stdout
    bpp, cx, cy = out[0] & 0x7F, out[1] | out[2] << 8, out[3]
    run_calc.half = bool(out[0] & 0x80)
    size = 320 * 240 * bpp // 8
    lifted = np.frombuffer(out, np.uint8, size, 4)
    drawn = np.frombuffer(out, np.uint8, size, 4 + size)
    return 0, (bpp, cx, cy), lifted, drawn


@pytest.mark.parametrize("name", ["256", "16", "2"])
@pytest.mark.parametrize("lz4", [True, False])
def test_c_decoder_matches_python(harness, name, lz4):
    mode = MODES[name]
    stride = mode.stride
    doc = page(7)[:, :stride]
    rng = np.random.default_rng(3)

    wire = b""
    model = None
    spot = (0, CURSOR_HIDDEN)
    frames = list(random_frames(11, 12, mode.fb_size))
    # Mix in scrolling frames so move ops reach the C decoder.
    frames += [doc[top:top + 240].ravel().copy() for top in (300, 290, 291, 200, 320, 100)]
    for index, frame in enumerate(frames):
        messages = b""
        if model is None:
            messages += bytes([MSG_MODE, mode.bpp]) + mode.palette_message()
            body = encode_frame(frame)
        else:
            move = find_scroll(frame.reshape(-1, stride), model.reshape(-1, stride))
            if move:
                moved = model.copy()
                apply_move(moved.reshape(-1, stride), *move)
                body = encode_frame(frame, moved, move_op(*move))
            else:
                body = encode_frame(frame, model)
        messages += body
        # Pointer positions slip in between packets, including mid-frame.
        for packet in packets(messages, lz4):
            if rng.random() < 0.5:
                spot = (int(rng.integers(0, 320)), int(rng.integers(0, 240)))
                wire += cursor_packet(*spot)
            wire += packet
        if index % 3 == 0:
            spot = (int(rng.integers(0, 320)), int(rng.integers(0, 240)))
            wire += cursor_packet(*spot)
        model = frame
    assert wire.count(b"\xc1") > 0

    status, state, lifted, drawn = run_calc(harness, wire)
    assert status == 0
    assert state == (mode.bpp, *spot)
    assert (lifted == model).all(), "pointer must lift off cleanly"

    # The drawn pointer only touches its own 8x12 box and starts with the outline colour.
    shown = mode.unpack(drawn).astype(int)
    clean = mode.unpack(lifted).astype(int)
    changed = np.argwhere(shown != clean)
    x, y = spot
    if len(changed):
        assert changed[:, 0].min() >= y and changed[:, 0].max() < y + 12
        assert changed[:, 1].min() >= x and changed[:, 1].max() < x + 8
    assert shown[y, x] == 0


@pytest.mark.parametrize("name", ["256", "16", "2"])
def test_c_pointer_at_screen_edges(harness, name):
    mode = MODES[name]
    frame = np.random.default_rng(4).integers(0, 256, mode.fb_size, dtype=np.uint8)
    setup = bytes([MSG_MODE, mode.bpp]) + mode.palette_message() + encode_frame(frame)
    for spot in ((0, 0), (319, 0), (0, 239), (319, 239), (313, 229), (317, 100)):
        # Moving the pointer around and back must leave the picture untouched.
        wire = packetize(setup) + cursor_packet(5, 5) + cursor_packet(*spot) * 2
        status, state, lifted, drawn = run_calc(harness, wire)
        assert status == 0 and state == (mode.bpp, *spot)
        assert (lifted == frame).all()
        assert mode.unpack(drawn)[spot[1], spot[0]] == 0
    status, state, lifted, drawn = run_calc(
        harness, packetize(setup) + cursor_packet(9, 9) + cursor_packet(0, CURSOR_HIDDEN))
    assert status == 0 and (drawn == frame).all()


@pytest.mark.parametrize("name", ["256", "16", "2"])
def test_c_half_resolution_mode(harness, name):
    """Video mode: a 160x120 picture shown with every pixel doubled."""
    full = MODES[name]
    half = half_mode(full)
    assert half.fb_size * 4 == full.fb_size and half.mode_message()[1] == full.bpp | 0x80
    rng = np.random.default_rng(12)

    def doubled(model):
        return half.unpack(model).repeat(2, axis=0).repeat(2, axis=1)

    # Full resolution first, then into video mode and through a run of changes.
    first = rng.integers(0, 256, full.fb_size, dtype=np.uint8)
    wire = packetize(full.mode_message() + full.palette_message() + encode_frame(first))
    wire += cursor_packet(100, 100)
    model = None
    for index, frame in enumerate(random_frames(13, 10, half.fb_size)):
        messages = b""
        if model is None:
            messages = half.mode_message() + half.palette_message()
        messages += encode_frame(frame, model)
        wire += packetize(messages)
        if index % 2:
            wire += cursor_packet(int(rng.integers(0, 320)), int(rng.integers(0, 240)))
        model = frame
        if index in (0, 4, 9):
            status, state, lifted, drawn = run_calc(harness, wire)
            assert status == 0 and state[0] == full.bpp and run_calc.half
            assert (full.unpack(lifted) == doubled(model)).all()
            assert full.unpack(drawn)[state[2], state[1]] == 0  # pointer on top, full size

    # A small change only redraws its own rows, and the result is still exact.
    frame = model.copy()
    frame[half.stride * 50 + 3] ^= 0xFF
    wire += packetize(encode_frame(frame, model))
    status, _, lifted, _ = run_calc(harness, wire)
    assert status == 0 and (full.unpack(lifted) == doubled(frame)).all()

    # And back to full resolution.
    last = rng.integers(0, 256, full.fb_size, dtype=np.uint8)
    wire += packetize(full.mode_message() + full.palette_message() + encode_frame(last))
    status, _, lifted, _ = run_calc(harness, wire)
    assert status == 0 and not run_calc.half and (lifted == last).all()


def test_half_modes_average_and_roundtrip():
    img = np.zeros((240, 320, 3), np.uint8)
    img[0:2, 0:2] = 255  # one whole low-res pixel white
    img[10, 10] = 255  # a quarter of another: averages to dark grey
    for name in ("256", "16", "2"):
        half = half_mode(MODES[name])
        fb = half.pack(img)
        assert fb.size == half.fb_size == MODES[name].fb_size // 4
        shown = half.to_rgb(fb)
        assert shown.shape == (240, 320, 3)
        assert shown[0, 0].tolist() == shown[1, 1].tolist() == [255, 255, 255]
        assert shown[10, 10].max() < 128 and shown[100, 100].tolist() == [0, 0, 0]


def test_c_decoder_rejects_bad_input(harness):
    frame = np.zeros(FB_SIZE, np.uint8)
    good = bytes([MSG_MODE, 8]) + encode_frame(frame)
    bad_streams = {
        "run past the end": b"F" + bytes([0x40, 0xFF, 0xFF, 1]) * 2 + bytes([OP_END]),
        "unknown message": b"Z",
        "unknown op": b"F" + bytes([0xC7]),
        "bad mode": bytes([MSG_MODE, 3]),
        "move off the bottom": b"F" + move_op(0, 200, 320, 40, 0, 1) + bytes([OP_END]),
        "move too wide": b"F" + move_op(300, 0, 21, 10, 0, 5) + bytes([OP_END]),
        "move up off the top": b"F" + move_op(0, 3, 320, 10, 0, -4) + bytes([OP_END]),
        "move off the left": b"F" + move_op(3, 0, 100, 10, -4, 0) + bytes([OP_END]),
        "move off the right": b"F" + move_op(200, 0, 100, 10, 21, 0) + bytes([OP_END]),
        "move by nothing": b"F" + move_op(0, 3, 320, 10, 0, 0) + bytes([OP_END]),
    }
    assert run_calc(harness, packetize(good))[0] == 0
    for name, messages in bad_streams.items():
        assert run_calc(harness, packetize(good + messages, lz4=False))[0] == 4, name

    # Malformed packets and LZ4 blocks.
    assert run_calc(harness, struct.pack("<H", 0))[0] == 4
    assert run_calc(harness, struct.pack("<H", 0x4000 | 4) + bytes(4))[0] == 4
    assert run_calc(harness, struct.pack("<H", PKT_MAX + 1) + bytes(PKT_MAX + 1))[0] == 4
    assert run_calc(harness, struct.pack("<HH", PKT_LZ4 | 4, PKT_MAX + 1) + bytes(4))[0] == 4
    rng = np.random.default_rng(6)
    packet = packetize(b"F" + bytes(5000) + bytes(rng.integers(0, 256, 3000, dtype=np.uint8)))
    assert struct.unpack_from("<H", packet)[0] & PKT_LZ4
    for _ in range(300):  # corrupted blocks must fail cleanly, never crash (ASan is on)
        broken = bytearray(packet)
        for _ in range(rng.integers(1, 4)):
            broken[rng.integers(4, len(broken))] = rng.integers(0, 256)
        assert run_calc(harness, bytes(broken))[0] in (0, 4)
    for cut in (1, 3, 10, len(packet) - 1):  # truncated input
        assert run_calc(harness, packet[:cut])[0] == 4


# -- colour -------------------------------------------------------------------


def test_palette():
    pal = palette_rgb()
    assert pal[0].tolist() == [0, 0, 0] and pal[255].tolist() == [255, 255, 255]
    rng = np.random.default_rng(3)
    img = rng.integers(0, 256, (240, 320, 3), dtype=np.uint8)
    for dither in (False, True):
        idx = quantize(img, dither)
        assert idx.shape == (240, 320) and idx.dtype == np.uint8
    # Quantisation error stays within about one palette step per channel.
    err = np.abs(pal[quantize(img)].astype(int) - img).max()
    assert err <= 34
    grey = np.full((4, 4, 3), 128, np.uint8)
    assert np.abs(pal[quantize(grey)].astype(int) - 128).max() <= 8


def test_colour_modes_pack_leftmost_pixel_low():
    img = np.zeros((240, 320, 3), np.uint8)
    img[0, 0] = 255  # only the top-left pixel is white
    img[1, 9] = 255
    assert MODES["256"].pack(img)[0] == 255
    grey = MODES["16"].pack(img)
    assert grey[0] == 0x0F and grey[160 + 4] == 0xF0 and len(grey) == 38400
    mono = MODES["2"].pack(img)
    assert mono[0] == 0x01 and mono[40 + 1] == 0x02 and len(mono) == 9600
    for mode in MODES.values():
        for dither in (False, True):
            fb = mode.pack(img, dither)
            assert fb.size == mode.fb_size and mode.to_rgb(fb).shape == (240, 320, 3)
        assert mode.to_rgb(mode.pack(img))[0, 0].tolist() == [255, 255, 255]
        assert len(mode.palette_message()) == 2 + 2 * (1 << mode.bpp)
