"""Fixed 256-colour palette and fast RGB -> palette index conversion.

Indices 0..239 are a 6x8x5 RGB cube (index = r*40 + g*5 + b), so index 0 is
black. Indices 240..255 are 16 extra greys, ending in pure white at 255,
because desktops are mostly grey and the cube alone has almost none.
"""

import numpy as np

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
