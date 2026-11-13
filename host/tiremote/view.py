"""Chooses which part of the desktop to show and renders it at 320x240."""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .protocol import LCD_H, LCD_W

ZOOM_SCALES = (8.0, 4.0, 2.0, 1.0)  # screen pixels per calculator pixel
EDGE_MARGIN = 0.2  # the view scrolls when the pointer gets this close to its edge

_CURSOR = (
    "X.......",
    "XX......",
    "X#X.....",
    "X##X....",
    "X###X...",
    "X####X..",
    "X#####X.",
    "X######X",
    "X###XXXX",
    "X#X#X...",
    "XX.X#X..",
    "....XX..",
)
_CURSOR_ART = np.array([[".X#".index(c) for c in row] for row in _CURSOR])

RESAMPLE = {
    "nearest": Image.Resampling.NEAREST,
    "box": Image.Resampling.BOX,
    "bilinear": Image.Resampling.BILINEAR,
    "lanczos": Image.Resampling.LANCZOS,
}


class Viewport:
    """Tracks the zoom level and the visible region of one monitor.

    All coordinates are in the monitor's own coordinate space (origin at its
    top-left corner), in the units the OS uses for pointer positions.
    """

    def __init__(self, width, height):
        self.width, self.height = width, height
        fit = max(width / LCD_W, height / LCD_H)
        self.scales = [fit] + [s for s in ZOOM_SCALES if s < fit * 0.8]
        self.level = 0
        self._origin = [0.0, 0.0]

    @property
    def scale(self):
        return self.scales[self.level]

    def zoom(self, step):
        self.level = max(0, min(len(self.scales) - 1, self.level + step))

    def label(self):
        if self.level == 0:
            return "fit"
        return "1:%g" % self.scale

    def layout(self, cursor):
        """Returns (region, out_size): the screen rectangle (x, y, w, h) to
        capture and the size it should be drawn at on the calculator."""
        scale = self.scale
        out_w = min(LCD_W, max(1, round(self.width / scale)))
        out_h = min(LCD_H, max(1, round(self.height / scale)))
        reg_w = min(self.width, round(out_w * scale))
        reg_h = min(self.height, round(out_h * scale))

        for axis, (pos, reg, full) in enumerate(((cursor[0], reg_w, self.width),
                                                 (cursor[1], reg_h, self.height))):
            margin = reg * EDGE_MARGIN
            origin = self._origin[axis]
            origin = min(origin, pos - margin)  # pointer near left/top edge
            origin = max(origin, pos + margin - reg)  # pointer near right/bottom edge
            self._origin[axis] = max(0.0, min(float(full - reg), origin))

        return (int(self._origin[0]), int(self._origin[1]), reg_w, reg_h), (out_w, out_h)


class Renderer:
    """Captures the viewport and turns it into a 320x240 RGB array."""

    def __init__(self, grabber, viewport, resample="lanczos"):
        self.grabber = grabber
        self.viewport = viewport
        self.resample = RESAMPLE[resample]
        try:
            self._font = ImageFont.load_default(size=9)
        except TypeError:  # Pillow < 10.1
            self._font = ImageFont.load_default()

    def render(self, cursor, status=""):
        """`cursor` is the pointer position in monitor coordinates."""
        (x, y, w, h), (out_w, out_h) = self.viewport.layout(cursor)
        shot = self.grabber.grab(x, y, w, h)
        if shot.size != (out_w, out_h):
            shot = shot.resize((out_w, out_h), self.resample, reducing_gap=2.0)

        canvas = Image.new("RGB", (LCD_W, LCD_H))
        left = (LCD_W - out_w) // 2
        canvas.paste(shot, (left, 0))

        if status:
            text = "%s  zoom:%s" % (status, self.viewport.label())
            draw = ImageDraw.Draw(canvas)
            box = draw.textbbox((0, 0), text, font=self._font)
            text_w, text_h = box[2] + 4, box[3] + 3
            draw.rectangle([0, LCD_H - text_h, text_w, LCD_H], fill=(0, 0, 0))
            draw.text((2, LCD_H - text_h + 1), text, font=self._font, fill=(255, 255, 255))

        frame = np.asarray(canvas).copy()
        scale_x, scale_y = w / out_w, h / out_h
        self._draw_cursor(frame, left + (cursor[0] - x) / scale_x, (cursor[1] - y) / scale_y)
        return frame

    @staticmethod
    def _draw_cursor(frame, cx, cy):
        cx, cy = int(cx), int(cy)
        art_h, art_w = _CURSOR_ART.shape
        x0, y0 = max(cx, 0), max(cy, 0)
        x1, y1 = min(cx + art_w, LCD_W), min(cy + art_h, LCD_H)
        if x0 >= x1 or y0 >= y1:
            return
        art = _CURSOR_ART[y0 - cy:y1 - cy, x0 - cx:x1 - cx]
        patch = frame[y0:y1, x0:x1]
        patch[art == 1] = 0
        patch[art == 2] = 255
