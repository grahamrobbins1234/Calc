"""Fixed 256-colour palette and fast RGB -> palette index conversion.

Indices 0..239 are a 6x8x5 RGB cube (index = r*40 + g*5 + b), so index 0 is
black. Indices 240..255 are 16 extra greys, ending in pure white at 255,
because desktops are mostly grey and the cube alone has almost none.
"""

import numpy as np

from .protocol import LCD_H, LCD_W, MODE_HALF, MSG_MODE, MSG_PALETTE

R_LEVELS, G_LEVELS, B_LEVELS = 6, 8, 5
CUBE_SIZE = R_LEVELS * G_LEVELS * B_LEVELS  # 240
GREY_BASE = CUBE_SIZE
GREY_COUNT = 256 - CUBE_SIZE  # 16
GREY_TOLERANCE = 12  # max channel spread for a pixel to count as grey


def _levels(n):
    return np.round(np.arange(n) * 255.0 / (n - 1)).astype(np.int32)


def _nearest_lut(levels):
    values = np.arange(256, dtype=np.int32)
    return np.abs(values[:, None] - levels[None, :]).argmin(axis=1).astype(np.uint8)


_R, _G, _B = _levels(R_LEVELS), _levels(G_LEVELS), _levels(B_LEVELS)
_GREYS = np.round((np.arange(GREY_COUNT) + 1) * 255.0 / GREY_COUNT).astype(np.int32)

_R_LUT = _nearest_lut(_R).astype(np.uint8) * (G_LEVELS * B_LEVELS)
_G_LUT = _nearest_lut(_G).astype(np.uint8) * B_LEVELS
_B_LUT = _nearest_lut(_B)

# Grey ramp candidates: black (index 0) followed by the 16 extra greys.
_grey_values = np.concatenate([[0], _GREYS])
_grey_indices = np.concatenate([[0], GREY_BASE + np.arange(GREY_COUNT)]).astype(np.uint8)
_GREY_LUT = _grey_indices[_nearest_lut(_grey_values)]

_BAYER = (
    np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32)
    + 0.5
) / 16.0 - 0.5


def palette_rgb():
    """The palette as a (256, 3) uint8 array."""
    pal = np.zeros((256, 3), dtype=np.uint8)
    idx = np.arange(CUBE_SIZE)
    pal[:CUBE_SIZE, 0] = _R[idx // (G_LEVELS * B_LEVELS)]
    pal[:CUBE_SIZE, 1] = _G[(idx // B_LEVELS) % G_LEVELS]
    pal[:CUBE_SIZE, 2] = _B[idx % B_LEVELS]
    pal[GREY_BASE:, :] = _GREYS[:, None]
    return pal


def palette_1555():
    """The palette as the 512 bytes the calculator LCD controller expects."""
    pal = palette_rgb().astype(np.uint16)
    words = ((pal[:, 0] >> 3) << 10) | ((pal[:, 1] >> 3) << 5) | (pal[:, 2] >> 3)
    return words.astype("<u2").tobytes()


def quantize(rgb, dither=False):
    """Convert an (H, W, 3) uint8 image to (H, W) uint8 palette indices."""
    if dither:
        h, w = rgb.shape[:2]
        tile = np.tile(_BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w, None]
        steps = np.array([255.0 / (R_LEVELS - 1), 255.0 / (G_LEVELS - 1), 255.0 / (B_LEVELS - 1)],
                         dtype=np.float32)
        # Leave grey pixels alone: the grey ramp is already fine-grained.
        spread = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2)
        coloured = (spread > GREY_TOLERANCE)[:, :, None]
        rgb = np.clip(rgb + tile * steps * coloured, 0, 255).astype(np.uint8)

    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    out = _R_LUT[r] + _G_LUT[g] + _B_LUT[b]

    hi = np.maximum(np.maximum(r, g), b)
    lo = np.minimum(np.minimum(r, g), b)
    grey = (hi - lo) <= GREY_TOLERANCE
    luma = ((r.astype(np.uint16) * 77 + g.astype(np.uint16) * 150 + b.astype(np.uint16) * 29) >> 8)
    return np.where(grey, _GREY_LUT[luma], out).astype(np.uint8)


def _luma(rgb):
    r, g, b = (rgb[:, :, i].astype(np.uint16) for i in range(3))
    return (r * 77 + g * 150 + b * 29) >> 8


def _bayer(h, w):
    return np.tile(_BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w]


class ColorMode:
    """A screen format the calculator can be switched to.

    `pack` turns an RGB image into the exact framebuffer bytes; pixels within
    a byte are stored leftmost-first in the lowest bits.
    """

    name = ""
    bpp = 8
    width, height = LCD_W, LCD_H  # size of the picture the frames describe
    half = False

    @property
    def stride(self):
        return self.width * self.bpp // 8

    @property
    def fb_size(self):
        return self.stride * self.height

    def mode_message(self):
        return bytes([MSG_MODE, self.bpp | (MODE_HALF if self.half else 0)])

    def palette_message(self):
        colours = self.palette_rgb().astype(np.uint16)
        words = ((colours[:, 0] >> 3) << 10) | ((colours[:, 1] >> 3) << 5) | (colours[:, 2] >> 3)
        return bytes([MSG_PALETTE, len(colours) & 0xFF]) + words.astype("<u2").tobytes()

    def to_rgb(self, fb):
        """Framebuffer bytes back to an (H, W, 3) image, as the LCD would show them."""
        return self.palette_rgb()[self.unpack(fb)]


class Colour256(ColorMode):
    name, bpp = "256", 8

    def palette_rgb(self):
        return palette_rgb()

    def pack(self, rgb, dither=False):
        return quantize(rgb, dither).ravel()

    def unpack(self, fb):
        return np.asarray(fb).reshape(self.height, self.width)


class Grey16(ColorMode):
    """16 greys at 4 bits per pixel: half the data, still good for text."""

    name, bpp = "16", 4

    def palette_rgb(self):
        return np.repeat((np.arange(16, dtype=np.uint8) * 17)[:, None], 3, axis=1)

    def pack(self, rgb, dither=False):
        luma = _luma(rgb).astype(np.float32)
        if dither:
            luma = luma + _bayer(*luma.shape) * 17
        level = np.clip((luma * 15 + 127) // 255, 0, 15).astype(np.uint8)
        return (level[:, 0::2] | (level[:, 1::2] << 4)).ravel()

    def unpack(self, fb):
        fb = np.asarray(fb).reshape(self.height, -1)
        out = np.empty((self.height, self.width), dtype=np.uint8)
        out[:, 0::2] = fb & 15
        out[:, 1::2] = fb >> 4
        return out


class Mono(ColorMode):
    """Black and white at 1 bit per pixel: an eighth of the data."""

    name, bpp = "2", 1

    def palette_rgb(self):
        return np.array([[0, 0, 0], [255, 255, 255]], dtype=np.uint8)

    def pack(self, rgb, dither=False):
        luma = _luma(rgb).astype(np.float32)
        if dither:
            luma = luma + _bayer(*luma.shape) * 255
        return np.packbits(luma >= 128, axis=1, bitorder="little").ravel()

    def unpack(self, fb):
        fb = np.asarray(fb).reshape(self.height, -1)
        return np.unpackbits(fb, axis=1, bitorder="little")


def half_mode(mode):
    """The half-resolution ("video") version of a colour mode: a 160x120
    picture the calculator shows with every pixel doubled. A quarter of the
    data per frame, for pictures that change all over, such as games."""
    if mode.half:
        return mode

    class Half(type(mode)):
        width, height = LCD_W // 2, LCD_H // 2
        half = True

        def pack(self, rgb, dither=False):
            h, w = rgb.shape[:2]
            small = rgb.reshape(h // 2, 2, w // 2, 2, 3).mean(axis=(1, 3)).astype(np.uint8)
            return super().pack(small, dither)

        def to_rgb(self, fb):
            small = super().to_rgb(fb)
            return small.repeat(2, axis=0).repeat(2, axis=1)

    return Half()


MODES = {mode.name: mode for mode in (Colour256(), Grey16(), Mono())}
MODE_ORDER = ["256", "16", "2"]
