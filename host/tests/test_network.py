"""Network mode: the host against the bridge's real link code, compiled natively."""

import hashlib
import hmac
import os
import shutil
import socket
import subprocess
import threading
import time
import zlib

import numpy as np
import pytest
from PIL import Image

from tiremote import net
from tiremote.inject import NullInjector
from tiremote.keymap import KeyMapper
from tiremote.protocol import LCD_H, LCD_W
from tiremote.session import Session
from tiremote.view import Renderer, Viewport

from fakecalc import FakeCalc

HERE = os.path.dirname(__file__)
BRIDGE = os.path.join(HERE, "..", "..", "bridge")
KEY = "correct horse battery"


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """bridge/main/link.c + sha256.c built for this machine. Needs miniz (MINIZ_DIR)."""
    compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    miniz = os.environ.get("MINIZ_DIR")
    if not compiler or not miniz:
        pytest.skip("needs a C compiler and MINIZ_DIR pointing at a miniz checkout")
    tmp = tmp_path_factory.mktemp("bridge")
    (tmp / "miniz_export.h").write_text("#define MINIZ_EXPORT\n")
    exe = tmp / "link_harness"
    main = os.path.join(BRIDGE, "main")
    subprocess.run([compiler, "-O1", "-Wall", "-Wextra", "-Werror", "-DMINIZ_NO_ARCHIVE_APIS",
                    "-I", str(tmp), "-I", miniz, "-I", main,
                    os.path.join(BRIDGE, "test", "link_harness.c"),
                    os.path.join(main, "link.c"), os.path.join(main, "sha256.c"),
                    os.path.join(miniz, "miniz_tinfl.c"), "-o", str(exe)], check=True)
    return str(exe)


def bridge_respond(harness, challenge, key):
    return subprocess.run([harness, "respond", key], input=challenge,
                          capture_output=True, check=True).stdout


def test_bridge_hmac_matches_host(harness):
    for key in ("short-key", KEY, "k" * 64, "long" * 40):
        a, b = socket.socketpair()
        result = {}
        thread = threading.Thread(
            target=lambda: result.update(flags=net.handshake(a, key)), daemon=True)
        thread.start()
        b.sendall(bridge_respond(harness, b.recv(64), key))
        thread.join(timeout=5)
        assert result == {"flags": net.FLAG_DEFLATE}
        assert b.recv(1) == bytes([net.FLAG_DEFLATE])
        a.close()
        b.close()


def test_wrong_key_and_garbage_are_rejected(harness):
    for reply in (lambda c: bridge_respond(harness, c, "not the key"),
                  lambda c: b"GET / HTTP/1.1\r\n" + bytes(40)):
        a, b = socket.socketpair()
        errors = []

        def run():
            try:
                net.handshake(a, KEY)
            except net.HandshakeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        b.sendall(reply(b.recv(64)))
        thread.join(timeout=5)
        assert len(errors) == 1
        a.close()
        b.close()


@pytest.mark.parametrize("chunk", [1, 13, 1460, 4096])
def test_bridge_inflates_host_stream(harness, chunk):
    a, b = socket.socketpair()
    b.settimeout(0.5)
    port = net.NetPort(a, net.FLAG_DEFLATE)
    rng = np.random.default_rng(chunk)
    # More than the 32 KB inflate window, mixing compressible and random data.
    messages = [bytes(rng.integers(0, 256, rng.integers(1, 9000), dtype=np.uint8)) if i % 3
                else bytes([i % 7]) * int(rng.integers(1, 30000)) for i in range(40)]
    wire = bytearray()

    def collect():
        try:
            while True:
                part = b.recv(65536)
                if not part:
                    return
                wire.extend(part)
        except OSError:
            return

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    for message in messages:
        port.write(message)
    reader.join(timeout=5)

    plain = b"".join(messages)
    assert len(wire) == port.wire_bytes < len(plain)
    decoded = subprocess.run([harness, "inflate", str(chunk)], input=bytes(wire),
                             capture_output=True, check=True).stdout
    assert decoded == plain
    a.close()
    b.close()


def test_bridge_rejects_corrupt_stream(harness):
    result = subprocess.run([harness, "inflate", "64"], input=b"\xff" * 64, capture_output=True)
    assert result.returncode == 4


class StaticGrabber:
    """A fake 640x480 desktop."""

    left = top = 0
    width, height = 640, 480

    def __init__(self):
        yy, xx = np.mgrid[0:480, 0:640]
        self.image = Image.fromarray(
            np.dstack([xx % 256, yy % 256, (xx + yy) % 256]).astype(np.uint8))

    def grab(self, x, y, w, h):
        return self.image.crop((x, y, x + w, y + h))


@pytest.mark.parametrize("compress", [True, False])
def test_session_over_tcp(compress):
    """Listener + Session against a Python stand-in for the bridge and calculator."""
    listener = net.Listener("127.0.0.1", 0, KEY, compress=compress, log=lambda *_: None)
    grabber = StaticGrabber()
    viewport = Viewport(grabber.width, grabber.height)
    injector = NullInjector((320, 240))
    mapper = KeyMapper(injector, on_zoom=viewport.zoom)
    done = {}

    def serve():
        link = None
        while link is None:
            link = listener.accept()
        port, _ = link
        session = Session(port, Renderer(grabber, viewport), injector, mapper,
                          fps=120, log=lambda *_: None)
        done["session"] = session
        session.run()
        port.close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()

    # The bridge: dial, answer the challenge, then pipe to the "calculator".
    sock = socket.create_connection(("127.0.0.1", listener.port))
    challenge = sock.recv(64)
    assert challenge[:5] == b"TIRD\x01" and len(challenge) == 21
    sock.sendall(b"TIRD\x01" + bytes([net.FLAG_DEFLATE])
                 + hmac.new(KEY.encode(), challenge[5:], hashlib.sha256).digest())
    flags = sock.recv(1)[0]
    assert flags == (net.FLAG_DEFLATE if compress else 0)

    calc_end, bridge_end = socket.socketpair()

    def pump_down():  # host -> calculator, inflating like the ESP32 does
        inflater = zlib.decompressobj(-15) if flags else None
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                bridge_end.sendall(inflater.decompress(data) if inflater else data)
        except OSError:
            pass

    def pump_up():  # calculator -> host
        try:
            while True:
                data = bridge_end.recv(4096)
                if not data:
                    break
                sock.sendall(data)
        except OSError:
            pass

    for target in (pump_down, pump_up):
        threading.Thread(target=target, daemon=True).start()

    def wait_for(predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    calc = FakeCalc(calc_end.fileno())
    calc.hello()
    assert wait_for(lambda: calc.frames >= 1 and "session" in done)
    session = done["session"]

    def in_sync():
        return calc.error is None and (calc.fb == session.shown).all()

    assert wait_for(in_sync)
    screen = calc.fb.reshape(LCD_H, LCD_W)
    assert screen[100, 10] != screen[100, 200]
    assert wait_for(lambda: calc.cursor == (160, 120))

    calc.keys("right")  # moves the (virtual, view-only) pointer
    assert wait_for(lambda: injector.position()[0] > 320)
    calc.keys()
    assert wait_for(lambda: calc.cursor[0] > 160 and in_sync())
    calc.close()

    # Dropping the connection ends the session cleanly.
    sock.shutdown(socket.SHUT_RDWR)
    sock.close()
    server.join(timeout=5)
    assert not server.is_alive()
    listener.close()
    calc_end.close()
    bridge_end.close()
