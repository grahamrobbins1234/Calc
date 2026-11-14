"""Screen capture via mss."""

import ctypes
import sys

from PIL import Image


def _mac_report(sct):
    """Everything macOS says about display sizes, for troubleshooting."""
    lines = []
    try:
        core = sct.core
        count = ctypes.c_uint32(0)
        displays = (ctypes.c_uint32 * 32)()
        core.CGGetActiveDisplayList(32, displays, ctypes.byref(count))
        for index in range(count.value):
            rect = core.CGRectStandardize(core.CGDisplayBounds(displays[index]))
            lines.append("CoreGraphics display %d: origin %d,%d size %dx%d rotation %s"
                         % (index + 1, rect.origin.x, rect.origin.y, rect.size.width,
                            rect.size.height, core.CGDisplayRotation(displays[index])))
    except Exception as exc:
        lines.append("CoreGraphics query failed: %r" % exc)
    try:
        from AppKit import NSScreen

        for index, screen in enumerate(NSScreen.screens()):
            frame = screen.frame()
            lines.append("NSScreen %d: origin %d,%d size %dx%d scale %s"
                         % (index + 1, frame.origin.x, frame.origin.y, frame.size.width,
                            frame.size.height, screen.backingScaleFactor()))
    except Exception as exc:
        lines.append("NSScreen query failed: %r" % exc)
    return lines


class MssGrabber:
    """Grabs rectangles of one monitor. Use from a single thread."""

    def __init__(self, monitor=1, size=None):
        import mss  # imported late: needs a display

        self._sct = (getattr(mss, "MSS", None) or mss.mss)()
        monitors = self._sct.monitors
        if not 0 <= monitor < len(monitors):
            raise ValueError("monitor %d not found (have 1..%d, or 0 for all)"
                             % (monitor, len(monitors) - 1))
        mon = monitors[monitor]
        self.monitor = monitor
        self.left, self.top = mon["left"], mon["top"]
        self.width, self.height = mon["width"], mon["height"]
        self.note = ""
        if size:
            self.width, self.height = size
        elif monitor and not self._fits(self.width, self.height) \
                and self._fits(self.height, self.width):
            # A rotated display reported with its sides swapped (seen on macOS).
            self.width, self.height = self.height, self.width
            self.note = "rotated display detected"

    def _fits(self, width, height):
        """Does a capture of this size come back the same shape? It does not
        when the rectangle hangs off the edge of the real display."""
        try:
            shot = self._sct.grab({"left": self.left, "top": self.top,
                                   "width": width, "height": height})
        except Exception:
            return False
        return abs(shot.width * height - shot.height * width) <= 0.02 * shot.height * width

    def report(self):
        """Lines describing what the system says about the displays."""
        lines = ["mss monitors: %s" % (self._sct.monitors,),
                 "using monitor %d at %d,%d size %dx%d %s"
                 % (self.monitor, self.left, self.top, self.width, self.height, self.note)]
        for width, height in ((self.width, self.height), (self.height, self.width)):
            try:
                shot = self._sct.grab({"left": self.left, "top": self.top,
                                       "width": width, "height": height})
                lines.append("capture of %dx%d returned %dx%d pixels"
                             % (width, height, shot.width, shot.height))
            except Exception as exc:
                lines.append("capture of %dx%d failed: %r" % (width, height, exc))
        if sys.platform == "darwin":
            lines += _mac_report(self._sct)
        return lines

    def grab(self, x, y, w, h):
        """Capture (x, y, w, h) in monitor coordinates as an RGB PIL image."""
        shot = self._sct.grab({"left": self.left + x, "top": self.top + y,
                               "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
