"""One connected calculator: answers frame requests and applies its key presses."""

import queue
import threading
import time

import serial

from .encoder import encode_frame
from .palette import palette_1555, quantize
from .protocol import KEY_GROUPS, MSG_BYE, MSG_HELLO, MSG_KEYS, MSG_PALETTE, MSG_REQ

IDLE_WAIT = 0.1  # how long to hold a request open waiting for the screen to change
TICK = 1 / 60


class Session:
    def __init__(self, port, renderer, injector, mapper, origin=(0, 0),
                 fps=20, dither=False, log=print):
        self.port = port  # an open serial.Serial (or anything with read/write)
        self.renderer = renderer
        self.injector = injector
        self.mapper = mapper
        self.origin = origin  # monitor's top-left corner in pointer coordinates
        self.frame_interval = 1.0 / fps
        self.dither = dither
        self.log = log
        self.frames = 0
        self.bytes_sent = 0
        self._requests = queue.Queue()
        self._stop = threading.Event()
        self._error = None

    def stop(self):
        self._stop.set()

    # -- calculator -> host -----------------------------------------------

    def _read_exact(self, count):
        data = b""
        while len(data) < count and not self._stop.is_set():
            data += self.port.read(count - len(data))
        return data

    def _reader(self):
        try:
            while not self._stop.is_set():
                head = self.port.read(1)
                if not head:
                    continue
                kind = head[0]
                if kind == MSG_HELLO:
                    self._read_exact(1)  # protocol version; only one exists so far
                    self._requests.put(True)
                elif kind == MSG_REQ:
                    self._requests.put(False)
                elif kind == MSG_KEYS:
                    matrix = self._read_exact(KEY_GROUPS)
                    if len(matrix) == KEY_GROUPS:
                        self.mapper.update(matrix)
                elif kind == MSG_BYE:
                    self.log("Calculator client exited.")
                    self.mapper.release_all()
        except (serial.SerialException, OSError) as exc:
            self._error = exc
        finally:
            self._stop.set()

    def _ticker(self):
        while not self._stop.wait(TICK):
            self.mapper.scale = self.renderer.viewport.scale
            try:
                self.mapper.tick()
            except Exception as exc:  # an input backend failure should not kill the stream
                self.log("Input error: %s" % exc)

    # -- host -> calculator -----------------------------------------------

    def _render(self):
        px, py = self.injector.position()
        cursor = (px - self.origin[0], py - self.origin[1])
        rgb = self.renderer.render(cursor, self.mapper.status())
        return quantize(rgb, self.dither).ravel()

    def run(self):
        """Serve until the calculator disconnects or stop() is called."""
        threads = [threading.Thread(target=self._reader, daemon=True),
                   threading.Thread(target=self._ticker, daemon=True)]
        for thread in threads:
            thread.start()

        shown = None  # what the calculator's screen currently holds
        last_sent = 0.0
        try:
            while not self._stop.is_set():
                try:
                    full = self._requests.get(timeout=0.2)
                except queue.Empty:
                    continue

                delay = last_sent + self.frame_interval - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

                if full or shown is None:
                    self.log("Calculator connected, streaming.")
                    shown = None
                    out = bytes([MSG_PALETTE]) + palette_1555()
                    frame = self._render()
                else:
                    out = b""
                    deadline = time.monotonic() + IDLE_WAIT
                    while True:
                        frame = self._render()
                        if (frame != shown).any() or time.monotonic() >= deadline:
                            break
                        time.sleep(0.01)

                out += encode_frame(frame, shown)
                try:
                    self.port.write(out)
                except serial.SerialTimeoutException:
                    # The calculator stopped reading; it will ask for a full frame.
                    shown = None
                    continue
                shown = frame
                last_sent = time.monotonic()
                self.frames += 1
                self.bytes_sent += len(out)
        except (serial.SerialException, OSError) as exc:
            self._error = exc
        finally:
            self._stop.set()
            for thread in threads:
                thread.join(timeout=1.0)
            self.mapper.release_all()
        return self._error
