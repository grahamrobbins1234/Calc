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

# How to turn the upright picture for each clockwise rotation.
_TURN = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}

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

    def __init__(self, width, height, rotation=0):
        self.width, self.height = width, height
        # With the calculator held sideways the picture area is 240 wide, 320 tall.
        self.rotation = rotation
        self.lcd_w, self.lcd_h = (LCD_H, LCD_W) if rotation in (90, 270) else (LCD_W, LCD_H)
        fit = max(width / self.lcd_w, height / self.lcd_h)
        self.scales = [fit] + [s for s in ZOOM_SCALES if s < fit * 0.8]
        self.level = 0
        self._origin = [0.0, 0.0]
        self.x_step = self.y_step = 1  # pan granularity, in picture pixels

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
        out_w = min(self.lcd_w, max(1, round(self.width / scale)))
        out_h = min(self.lcd_h, max(1, round(self.height / scale)))
        reg_w = min(self.width, round(out_w * scale))
        reg_h = min(self.height, round(out_h * scale))

        for axis, (pos, reg, full) in enumerate(((cursor[0], reg_w, self.width),
                                                 (cursor[1], reg_h, self.height))):
            margin = reg * EDGE_MARGIN
            origin = self._origin[axis]
            origin = min(origin, pos - margin)  # pointer near left/top edge
            origin = max(origin, pos + margin - reg)  # pointer near right/bottom edge
            self._origin[axis] = max(0.0, min(float(full - reg), origin))

        # Pan in whole calculator pixels, so a panned picture is the old one
        # shifted exactly and can be moved on the calculator instead of resent.
        # Along framebuffer rows that also has to be whole bytes (x_step / y_step).
        step = scale if float(scale).is_integer() else 1
        x = int(self._origin[0] // (step * self.x_step) * (step * self.x_step))
        y = int(self._origin[1] // (step * self.y_step) * (step * self.y_step))
        return (x, y, reg_w, reg_h), (out_w, out_h)


class Renderer:
    """Captures the viewport and turns it into a 320x240 RGB array."""

    def __init__(self, grabber, viewport, resample="lanczos"):
        self.grabber = grabber
        self.viewport = viewport
        self.resample = RESAMPLE[resample]
        self._mapping = None
        self.mapping = None  # (x, y, scale_x, scale_y, left, out_w, out_h) of the last render
        try:
            self._font = ImageFont.load_default(size=9)
        except TypeError:  # Pillow < 10.1
            self._font = ImageFont.load_default()

    def render(self, cursor, status="", draw_cursor=False):
        """`cursor` is the pointer position in monitor coordinates.

        The calculator draws its own pointer, so it is only painted into the
        picture on request (snapshots).
        """
        (x, y, w, h), (out_w, out_h) = self.viewport.layout(cursor)
        shot = self.grabber.grab(x, y, w, h)
        if shot.size != (out_w, out_h):
            shot = shot.resize((out_w, out_h), self.resample, reducing_gap=2.0)

        view_w, view_h = self.viewport.lcd_w, self.viewport.lcd_h
        canvas = Image.new("RGB", (view_w, view_h))
        left = (view_w - out_w) // 2
        canvas.paste(shot, (left, 0))
        self._mapping = (x, y, w / out_w, h / out_h, left, out_w, out_h)
        self.mapping = self._mapping

        if status:
            text = "%s  zoom:%s" % (status, self.viewport.label())
            draw = ImageDraw.Draw(canvas)
            box = draw.textbbox((0, 0), text, font=self._font)
            text_w, text_h = box[2] + 4, box[3] + 3
            draw.rectangle([0, view_h - text_h, text_w, view_h], fill=(0, 0, 0))
            draw.text((2, view_h - text_h + 1), text, font=self._font, fill=(255, 255, 255))

        if self.viewport.rotation:
            canvas = canvas.transpose(_TURN[self.viewport.rotation])
        frame = np.asarray(canvas).copy()
        if draw_cursor:
            spot = self.to_lcd(cursor)
            if spot:
                self._draw_cursor(frame, *spot)
        return frame

    def _turn_point(self, px, py):
        """A pixel of the upright picture -> where it is in the framebuffer."""
        view_w, view_h = self.viewport.lcd_w, self.viewport.lcd_h
        rotation = self.viewport.rotation
        if rotation == 90:
            return view_h - 1 - py, px
        if rotation == 180:
            return view_w - 1 - px, view_h - 1 - py
        if rotation == 270:
            return py, view_w - 1 - px
        return px, py

    def turn_vector(self, dx, dy):
        """A movement in the upright picture -> the same movement in the framebuffer."""
        rotation = self.viewport.rotation
        if rotation == 90:
            return -dy, dx
        if rotation == 180:
            return -dx, -dy
        if rotation == 270:
            return dy, -dx
        return dx, dy

    def picture_rect(self, mapping):
        """Framebuffer rectangle (x0, y0, x1, y1) the desktop picture occupies."""
        _, _, _, _, left, out_w, out_h = mapping
        ax, ay = self._turn_point(left, 0)
        bx, by = self._turn_point(left + out_w - 1, out_h - 1)
        return min(ax, bx), min(ay, by), max(ax, bx) + 1, max(ay, by) + 1

    def to_lcd(self, cursor):
        """Where a monitor position lands on the calculator screen in the most
        recently rendered view, or None if it is outside the picture."""
        if self._mapping is None:
            return None
        x, y, scale_x, scale_y, left, out_w, out_h = self._mapping
        cx = int((cursor[0] - x) // scale_x)
        cy = int((cursor[1] - y) // scale_y)
        if not (0 <= cx < out_w and 0 <= cy < out_h):
            return None
        return self._turn_point(left + cx, cy)

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
