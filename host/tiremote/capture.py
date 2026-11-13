"""Screen capture via mss."""

import ctypes
import sys

from PIL import Image


def _mac_display_size(sct, monitor):
    """The true size of a display on macOS, or None.

    mss swaps width and height for displays rotated by 90 degrees, but macOS
    already reports rotated bounds, so the swap makes them wrong. Ask macOS
    directly, the same way mss does but without the swap.
    """
    try:
        core = sct.core
        count = ctypes.c_uint32(0)
        displays = (ctypes.c_uint32 * 32)()
        core.CGGetActiveDisplayList(32, displays, ctypes.byref(count))
        if not 1 <= monitor <= count.value:
            return None
        rect = core.CGRectStandardize(core.CGDisplayBounds(displays[monitor - 1]))
        return int(rect.size.width), int(rect.size.height)
    except Exception:
        return None


class MssGrabber:
    """Grabs rectangles of one monitor. Use from a single thread."""

    def __init__(self, monitor=1):
        import mss  # imported late: needs a display

        self._sct = (getattr(mss, "MSS", None) or mss.mss)()
        monitors = self._sct.monitors
        if not 0 <= monitor < len(monitors):
            raise ValueError("monitor %d not found (have 1..%d, or 0 for all)"
                             % (monitor, len(monitors) - 1))
        mon = monitors[monitor]
        self.left, self.top = mon["left"], mon["top"]
        self.width, self.height = mon["width"], mon["height"]
        if sys.platform == "darwin":
            true_size = _mac_display_size(self._sct, monitor)
            if true_size and true_size != (self.width, self.height):
                self.width, self.height = true_size

    def grab(self, x, y, w, h):
        """Capture (x, y, w, h) in monitor coordinates as an RGB PIL image."""
        shot = self._sct.grab({"left": self.left + x, "top": self.top + y,
                               "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
