"""Fixed 256-colour palette and fast RGB -> palette index conversion.

Indices 0..239 are a 6x8x5 RGB cube (index = r*40 + g*5 + b), so index 0 is
black. Indices 240..255 are 16 extra greys, ending in pure white at 255,
because desktops are mostly grey and the cube alone has almost none.
"""

import numpy as np

from .protocol import LCD_H, LCD_W, MODE_HALF, MODE_TURN_SHIFT, MSG_MODE, MSG_PALETTE, MSG_SCALE

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
    label = ""  # short form for the status line
    bpp = 8
    width, height = LCD_W, LCD_H  # size of the picture the frames describe
    shift = 0  # 1: half-size picture, 2: quarter-size, enlarged by the calculator

    @property
    def half(self):
        return self.shift > 0

    @property
    def stride(self):
        return self.width * self.bpp // 8

    @property
    def fb_size(self):
        return self.stride * self.height

    def mode_message(self, turns=0):
        """`turns`: quarter-turns clockwise the picture is rotated by."""
        return bytes([MSG_MODE, self.bpp | (MODE_HALF if self.half else 0)
                      | (turns & 3) << MODE_TURN_SHIFT])

    def scale_message(self):
        """Says how small this mode's pictures are (needed for quarter size)."""
        return bytes([MSG_SCALE, self.shift]) if self.shift else b""

    def palette_message(self):
        colours = self.palette_rgb().astype(np.uint16)
        words = ((colours[:, 0] >> 3) << 10) | ((colours[:, 1] >> 3) << 5) | (colours[:, 2] >> 3)
        return bytes([MSG_PALETTE, len(colours) & 0xFF]) + words.astype("<u2").tobytes()

    def to_rgb(self, fb):
        """Framebuffer bytes back to an (H, W, 3) image, as the LCD would show them."""
        return self.palette_rgb()[self.unpack(fb)]


class Colour256(ColorMode):
    name, label, bpp = "256", "256c", 8

    def palette_rgb(self):
        return palette_rgb()

    def pack(self, rgb, dither=False):
        return quantize(rgb, dither).ravel()

    def pack_indices(self, index):
        return np.ascontiguousarray(index, dtype=np.uint8).ravel()

    def unpack(self, fb):
        return np.asarray(fb).reshape(self.height, self.width)


class Grey16(ColorMode):
    """16 greys at 4 bits per pixel: half the data, still good for text."""

    name, label, bpp = "16", "16g", 4

    def palette_rgb(self):
        return np.repeat((np.arange(16, dtype=np.uint8) * 17)[:, None], 3, axis=1)

    def pack(self, rgb, dither=False):
        luma = _luma(rgb).astype(np.float32)
        if dither:
            luma = luma + _bayer(*luma.shape) * 17
        level = np.clip((luma * 15 + 127) // 255, 0, 15).astype(np.uint8)
        return (level[:, 0::2] | (level[:, 1::2] << 4)).ravel()

    def pack_indices(self, index):
        return (index[:, 0::2] | (index[:, 1::2] << 4)).astype(np.uint8).ravel()

    def unpack(self, fb):
        fb = np.asarray(fb).reshape(self.height, -1)
        out = np.empty((self.height, self.width), dtype=np.uint8)
        out[:, 0::2] = fb & 15
        out[:, 1::2] = fb >> 4
        return out


class Colour16(Grey16):
    """16 colours at 4 bits per pixel: half the data of 256 colours, and
    still in colour. Coarse, so gradients band unless dithering is on."""

    name, label = "16c", "16c"

    # Black first and white last: the calculator draws its pointer with them.
    _PALETTE = np.array([
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
        (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)], dtype=np.uint8)
    _lut = None

    def palette_rgb(self):
        return self._PALETTE

    @classmethod
    def _nearest(cls):
        """Nearest palette entry for every colour, at 5 bits per channel."""
        if cls._lut is None:
            steps = (np.arange(32) * 255 // 31).astype(np.int32)
            grid = np.stack(np.meshgrid(steps, steps, steps, indexing="ij"), axis=-1)
            diff = grid[:, :, :, None, :] - cls._PALETTE.astype(np.int32)[None, None, None]
            # Weighted so brightness errors count more than hue errors.
            dist = (diff ** 2 * np.array([3, 6, 2])).sum(axis=-1)
            cls._lut = dist.argmin(axis=-1).astype(np.uint8)
        return cls._lut

    def pack(self, rgb, dither=False):
        lut = self._nearest()
        q = rgb >> 3
        level = lut[q[:, :, 0], q[:, :, 1], q[:, :, 2]]
        if dither:
            # Only where the palette is a poor match, so flat areas that are
            # already (nearly) a palette colour, like white pages, stay clean.
            error = np.abs(rgb.astype(np.int16) - self.palette_rgb()[level]).max(axis=2)
            noise = _bayer(*rgb.shape[:2])[:, :, None] * 128
            q = np.clip(rgb + noise, 0, 255).astype(np.uint8) >> 3
            level = np.where(error > 40, lut[q[:, :, 0], q[:, :, 1], q[:, :, 2]], level)
        return (level[:, 0::2] | (level[:, 1::2] << 4)).ravel()


class Adaptive16(Colour16):
    """16 colours chosen from what is on screen, at 4 bits per pixel.

    Made for games with a limited, flat-shaded look (it was tuned on
    PolyTrack): half the data of 256 colours, and because the 16 colours are
    the picture's own, large areas come out as flat runs that compress well
    without looking wrong. The palette is re-chosen when the scene changes
    enough that it no longer fits, which costs one full refresh.
    """

    name, label = "16a", "16a"
    adaptive = True

    REFIT_MIN_INTERVAL = 1.5  # seconds between checks
    REFIT_RATIO = 1.5  # a fresh palette must beat the current one by this much

    def __init__(self):
        # Shared by the full-size and low-resolution versions of this mode.
        self.state = {"palette": Colour16._PALETTE, "lut": None, "when": -1e9}

    def palette_rgb(self):
        return self.state["palette"]

    def _nearest(self):
        return self.state["lut"]

    @staticmethod
    def _build_lut(palette):
        steps = (np.arange(32) * 255 // 31).astype(np.int32)
        grid = np.stack(np.meshgrid(steps, steps, steps, indexing="ij"), axis=-1)
        diff = grid[:, :, :, None, :] - palette.astype(np.int32)[None, None, None]
        return (diff ** 2 * np.array([3, 6, 2])).sum(axis=-1).argmin(axis=-1).astype(np.uint8)

    def _error(self, sample):
        q = sample >> 3
        index = self.state["lut"][q[:, :, 0], q[:, :, 1], q[:, :, 2]]
        return float(np.abs(sample.astype(np.int16) - self.state["palette"][index]).mean())

    @staticmethod
    def _choose(pixels):
        """16 colours for these pixels. Starts from colours that are as
        different from each other as possible, so small but distinct things
        (a red kerb, a white line) get a colour of their own instead of
        sixteen shades of the sky, then settles each onto the average of the
        pixels nearest to it."""
        weights = np.array([3.0, 6.0, 2.0], dtype=np.float32) ** 0.5
        points = pixels[:: max(1, len(pixels) // 6000)] * weights
        centres = [points.mean(axis=0)]
        distance = ((points - centres[0]) ** 2).sum(axis=1)
        for _ in range(15):
            centres.append(points[int(distance.argmax())])
            distance = np.minimum(distance, ((points - centres[-1]) ** 2).sum(axis=1))
        centres = np.array(centres)
        for _ in range(6):
            nearest = ((points[:, None, :] - centres[None]) ** 2).sum(axis=2).argmin(axis=1)
            for k in range(16):
                members = points[nearest == k]
                if len(members):
                    centres[k] = members.mean(axis=0)
        return np.clip(np.round(centres / weights), 0, 255).astype(np.uint8)

    def _adopt(self, colours, sample, now):
        # Darkest first and brightest last: the calculator draws its pointer
        # outline with colour 0 and its fill with colour 15.
        luma = colours.astype(np.int32) @ np.array([77, 150, 29])
        colours = colours[np.argsort(luma, kind="stable")]
        self.state.update(palette=colours, lut=self._build_lut(colours), when=now)

    def refit(self, rgb, now, force=False):
        """Re-choose the palette from `rgb` if a fresh choice would fit the
        picture clearly better than the current one. Returns True if the
        palette changed (the screen must then be resent)."""
        state = self.state
        sample = np.ascontiguousarray(rgb[::2, ::2])
        if state["lut"] is None or force:
            self._adopt(self._choose(sample.reshape(-1, 3).astype(np.float32)), sample, now)
            return True
        if now - state["when"] < self.REFIT_MIN_INTERVAL:
            return False
        state["when"] = now  # whatever happens, do not look again for a while
        current = self._error(sample)
        if current < 2:
            return False  # already as good as it gets
        old = (state["palette"], state["lut"])
        self._adopt(self._choose(sample.reshape(-1, 3).astype(np.float32)), sample, now)
        if current > self._error(sample) * self.REFIT_RATIO + 2:
            return True
        state["palette"], state["lut"] = old  # not worth a full refresh
        return False

    def pack(self, rgb, dither=False):
        if self.state["lut"] is None:
            self.refit(rgb, 0.0, force=True)
        return super().pack(rgb, dither)


class Mono(ColorMode):
    """Black and white at 1 bit per pixel: an eighth of the data."""

    name, label, bpp = "2", "bw", 1

    def palette_rgb(self):
        return np.array([[0, 0, 0], [255, 255, 255]], dtype=np.uint8)

    def pack(self, rgb, dither=False):
        luma = _luma(rgb).astype(np.float32)
        if dither:
            luma = luma + _bayer(*luma.shape) * 255
        return np.packbits(luma >= 128, axis=1, bitorder="little").ravel()

    def pack_indices(self, index):
        return np.packbits(index.astype(bool), axis=1, bitorder="little").ravel()

    def unpack(self, fb):
        fb = np.asarray(fb).reshape(self.height, -1)
        return np.unpackbits(fb, axis=1, bitorder="little")


_scaled = {}


def scaled_mode(mode, shift):
    """The low-resolution ("video") version of a colour mode: a picture of
    1/2 (shift 1) or 1/4 (shift 2) the width and height, which the
    calculator enlarges. A quarter or a sixteenth of the data per frame, for
    pictures that change all over, such as games."""
    base = MODES[mode.name]
    if not shift:
        return base
    if (base.name, shift) not in _scaled:
        factor = 1 << shift

        class Scaled(type(base)):
            width, height = LCD_W >> shift, LCD_H >> shift

            def pack(self, rgb, dither=False):
                h, w = rgb.shape[:2]
                small = rgb.reshape(h // factor, factor, w // factor, factor, 3)
                return super().pack(small.mean(axis=(1, 3)).astype(np.uint8), dither)

            def to_rgb(self, fb):
                small = super().to_rgb(fb)
                return small.repeat(factor, axis=0).repeat(factor, axis=1)

            def enlarged(self, fb):
                """The full-size framebuffer the calculator shows for `fb`."""
                index = self.unpack(fb).repeat(factor, axis=0).repeat(factor, axis=1)
                return base.pack_indices(index)

        Scaled.shift = shift
        scaled = Scaled()
        if hasattr(base, "state"):
            scaled.state = base.state  # one palette for every size of the mode
        _scaled[base.name, shift] = scaled
    return _scaled[base.name, shift]


def half_mode(mode):
    return scaled_mode(mode, 1)


MODES = {mode.name: mode for mode in (Colour256(), Adaptive16(), Colour16(), Grey16(), Mono())}
MODE_ORDER = ["256", "16a", "16c", "16", "2"]
