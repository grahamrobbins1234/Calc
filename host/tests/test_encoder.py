import os
import shutil
import subprocess

import numpy as np
import pytest

from tiremote.encoder import EMPTY_FRAME, decode_frame, encode_frame
from tiremote.palette import palette_1555, palette_rgb, quantize
from tiremote.protocol import FB_SIZE, MSG_PALETTE

CALC_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "calc", "src")


def roundtrip(new, old):
    data = encode_frame(new, old)
    fb = np.zeros(FB_SIZE, np.uint8) if old is None else old.copy()
    decode_frame(data, fb)
    assert (fb == new).all()
    return data


def random_frames(seed, count):
    rng = np.random.default_rng(seed)
    frame = rng.integers(0, 4, FB_SIZE, dtype=np.uint8)
    for _ in range(count):
        frame = frame.copy()
        for _ in range(rng.integers(0, 30)):
            start, length = rng.integers(0, FB_SIZE), rng.integers(1, 3000)
            span = frame[start:start + length]
            span[:] = rng.integers(0, 256) if rng.random() < 0.5 else \
                rng.integers(0, 256, span.size)
        yield frame


def test_unchanged_frame_is_empty():
    frame = np.arange(FB_SIZE, dtype=np.uint32).astype(np.uint8)
    assert encode_frame(frame, frame) == EMPTY_FRAME


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


def test_random_deltas():
    previous = None
    for frame in random_frames(1, 150):
        roundtrip(frame, previous)
        roundtrip(frame, None)
        previous = frame


@pytest.mark.parametrize("chunk", [1, 7, 64, 4096])
def test_c_decoder_matches(tmp_path, chunk):
    compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not compiler:
        pytest.skip("no C compiler")
    exe = tmp_path / "harness"
    subprocess.run([compiler, "-O1", "-Wall", "-Wextra", "-Werror", "-I", CALC_SRC,
                    os.path.join(os.path.dirname(__file__), "decode_harness.c"),
                    os.path.join(CALC_SRC, "decode.c"), "-o", str(exe)], check=True)

    stream = bytes([MSG_PALETTE]) + palette_1555()
    previous = None
    for frame in random_frames(2, 25):
        stream += encode_frame(frame, previous)
        previous = frame
    result = subprocess.run([str(exe), str(chunk)], input=stream, capture_output=True, check=True)
    assert result.stdout == previous.tobytes()


def test_c_decoder_rejects_overrun(tmp_path):
    compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not compiler:
        pytest.skip("no C compiler")
    exe = tmp_path / "harness"
    subprocess.run([compiler, "-I", CALC_SRC,
                    os.path.join(os.path.dirname(__file__), "decode_harness.c"),
                    os.path.join(CALC_SRC, "decode.c"), "-o", str(exe)], check=True)
    bad = b"F" + bytes([0x40, 0xFF, 0xFF, 1]) * 2 + bytes([0xC0])  # 131070 pixels of runs
    assert subprocess.run([str(exe)], input=bad, capture_output=True).returncode == 4


def test_palette():
    pal = palette_rgb()
    assert pal[0].tolist() == [0, 0, 0] and pal[255].tolist() == [255, 255, 255]
    assert len(palette_1555()) == 512
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
