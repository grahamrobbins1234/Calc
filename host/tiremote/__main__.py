"""Command line entry point: python -m tiremote"""

import argparse
import sys
import time

import numpy as np

from . import __version__
from .keymap import KeyMapper
from .protocol import USB_PID, USB_VID
from .view import RESAMPLE, Renderer, Viewport


def find_port():
    from serial.tools import list_ports

    for info in list_ports.comports():
        if info.vid == USB_VID and info.pid == USB_PID:
            return info.device
    return None


def list_ports_and_exit():
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
    for info in ports:
        mark = "  <-- calculator" if (info.vid, info.pid) == (USB_VID, USB_PID) else ""
        print("%s  %s%s" % (info.device, info.description, mark))
    sys.exit(0)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="tiremote",
        description="Share this computer's screen, mouse and keyboard with a "
                    "TI-84 Plus CE running the REMOTE program over USB.")
    parser.add_argument("--port", help="serial port of the calculator (default: auto-detect)")
    parser.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    parser.add_argument("--monitor", type=int, default=1,
                        help="monitor to share: 1 = primary, 2.. = others, 0 = all (default: 1)")
    parser.add_argument("--fps", type=float, default=20, help="maximum frames per second")
    parser.add_argument("--dither", action="store_true",
                        help="dither colours (smoother gradients, slower updates)")
    parser.add_argument("--filter", choices=sorted(RESAMPLE), default="lanczos",
                        help="downscaling filter (default: lanczos)")
    parser.add_argument("--view-only", action="store_true",
                        help="show the screen but ignore the calculator's keys")
    parser.add_argument("--snapshot", metavar="FILE",
                        help="save what the calculator would show as an image and exit")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def make_injector(args, grabber):
    from .inject import NullInjector, PynputInjector

    centre = (grabber.left + grabber.width // 2, grabber.top + grabber.height // 2)
    if args.view_only:
        return NullInjector(centre)
    try:
        return PynputInjector()
    except Exception as exc:
        print("Cannot control mouse/keyboard (%s); running view-only." % exc, file=sys.stderr)
        return NullInjector(centre)


def main(argv=None):
    args = parse_args(argv)
    if args.list_ports:
        list_ports_and_exit()

    import serial

    from .capture import MssGrabber
    from .palette import palette_rgb, quantize
    from .session import Session

    grabber = MssGrabber(args.monitor)
    viewport = Viewport(grabber.width, grabber.height)
    renderer = Renderer(grabber, viewport, args.filter)
    injector = make_injector(args, grabber)
    mapper = KeyMapper(injector, on_zoom=viewport.zoom)
    origin = (grabber.left, grabber.top)

    if args.snapshot:
        from PIL import Image

        px, py = injector.position()
        rgb = renderer.render((px - origin[0], py - origin[1]), mapper.status())
        Image.fromarray(palette_rgb()[quantize(rgb, args.dither)]).save(args.snapshot)
        print("Saved %s" % args.snapshot)
        return 0

    print("Sharing monitor %d (%dx%d). Press Ctrl+C to stop."
          % (args.monitor, grabber.width, grabber.height))
    waiting_shown = False
    try:
        while True:
            port_name = args.port or find_port()
            if port_name is None:
                if not waiting_shown:
                    print("Waiting for the calculator: plug it in and run REMOTE "
                          "(prgmREMOTE, or via Cesium/arTIfiCE)...")
                    waiting_shown = True
                time.sleep(1.0)
                continue
            try:
                port = serial.Serial(port_name, 115200, timeout=0.2, write_timeout=2.0)
            except (serial.SerialException, OSError) as exc:
                if not waiting_shown:
                    print("Cannot open %s: %s" % (port_name, exc))
                    waiting_shown = True
                time.sleep(1.0)
                continue

            waiting_shown = False
            print("Opened %s." % port_name)
            session = Session(port, renderer, injector, mapper, origin,
                              fps=args.fps, dither=args.dither)
            started = time.monotonic()
            try:
                port.reset_input_buffer()
                error = session.run()
            finally:
                session.stop()
                try:
                    port.close()
                except Exception:
                    pass
            elapsed = max(time.monotonic() - started, 1e-6)
            print("Disconnected (%s). %d frames, %.1f fps, %.0f KB/s average."
                  % (error or "stopped", session.frames, session.frames / elapsed,
                     session.bytes_sent / elapsed / 1024))
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopped.")
        mapper.release_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
