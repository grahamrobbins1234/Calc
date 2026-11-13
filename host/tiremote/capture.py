"""Screen capture via mss."""

from PIL import Image


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

    def grab(self, x, y, w, h):
        """Capture (x, y, w, h) in monitor coordinates as an RGB PIL image."""
        shot = self._sct.grab({"left": self.left + x, "top": self.top + y,
                               "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
