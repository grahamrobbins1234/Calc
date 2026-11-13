"""Command line entry point: python -m tiremote"""

import argparse
import os
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
    parser.add_argument("--listen", metavar="[ADDR:]PORT", nargs="?", const="",
                        help="network mode: wait for a Wi-Fi bridge to connect "
                             "(default port 8484) instead of using a USB cable")
    parser.add_argument("--key", help="shared key for --listen (or set TIREMOTE_KEY)")
    parser.add_argument("--no-compress", action="store_true",
                        help="with --listen: do not compress the stream to the bridge")
    parser.add_argument("--monitor", type=int, default=1,
                        help="monitor to share: 1 = primary, 2.. = others, 0 = all (default: 1)")
    parser.add_argument("--fps", type=float, default=20, help="maximum frames per second")
    parser.add_argument("--colors", choices=["256", "16", "2"], default="256",
                        help="starting colour mode: 256 colours, 16 greys (half the data) or "
                             "2 = black and white (an eighth); [2nd]+[clear] cycles it")
    parser.add_argument("--no-lz4", action="store_true",
                        help="do not LZ4-compress data for the calculator")
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
    from .palette import MODES
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
        mode = MODES[args.colors]
        rgb = renderer.render((px - origin[0], py - origin[1]),
                              "%s  %sc" % (mapper.status(), mode.name), draw_cursor=True)
        Image.fromarray(mode.to_rgb(mode.pack(rgb, args.dither))).save(args.snapshot)
        print("Saved %s" % args.snapshot)
        return 0

    listener = None
    if args.listen is not None:
        from .net import Listener, parse_listen

        key = args.key or os.environ.get("TIREMOTE_KEY", "")
        if len(key) < 8:
            sys.exit("--listen needs a shared key of at least 8 characters "
                     "(--key or the TIREMOTE_KEY environment variable).")
        address, tcp_port = parse_listen(args.listen)
        listener = Listener(address, tcp_port, key, compress=not args.no_compress)

    print("Sharing monitor %d (%dx%d). Press Ctrl+C to stop."
          % (args.monitor, grabber.width, grabber.height))
    if listener:
        print("Waiting for the Wi-Fi bridge on port %d. The stream is not encrypted: "
              "use it on a network you trust." % listener.port)

    def connect_serial(quiet):
        """Open the calculator's USB serial port, or explain (once) why not."""
        port_name = args.port or find_port()
        if port_name is None:
            if not quiet:
                print("Waiting for the calculator: plug it in and run REMOTE "
                      "(prgmREMOTE, or via Cesium/arTIfiCE)...")
            return None
        try:
            port = serial.Serial(port_name, 115200, timeout=0.2, write_timeout=2.0)
            port.reset_input_buffer()
        except (serial.SerialException, OSError) as exc:
            if not quiet:
                print("Cannot open %s: %s" % (port_name, exc))
            return None
        return port, port_name

    waiting_shown = False
    try:
        while True:
            if listener:
                link = listener.accept()
            else:
                link = connect_serial(waiting_shown)
                if link is None:
                    waiting_shown = True
                    time.sleep(1.0)
            if link is None:
                continue

            port, label = link
            waiting_shown = False
            print("Connected: %s." % label)
            session = Session(port, renderer, injector, mapper, origin,
                              fps=args.fps, dither=args.dither, colors=args.colors,
                              lz4=not args.no_lz4)
            started = time.monotonic()
            try:
                error = session.run()
            finally:
                session.stop()
                try:
                    port.close()
                except Exception:
                    pass
            elapsed = max(time.monotonic() - started, 1e-6)
            sent = getattr(port, "wire_bytes", session.bytes_sent)
            print("Disconnected (%s). %d frames, %.1f fps, %.0f KB/s average."
                  % (error or "stopped", session.frames, session.frames / elapsed,
                     sent / elapsed / 1024))
            if not listener:
                time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopped.")
        mapper.release_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
