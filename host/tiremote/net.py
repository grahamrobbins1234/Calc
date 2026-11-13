"""Network transport: a Wi-Fi bridge (ESP32) dials in over TCP.

Mirrors bridge/main/link.h. The bridge proves it knows the shared key with an
HMAC challenge before anything it sends is acted on. The traffic itself is
not encrypted.
"""

import hashlib
import hmac
import os
import select
import socket
import zlib

MAGIC = b"TIRD"
VERSION = 1
FLAG_DEFLATE = 0x01
NONCE_LEN = 16
RESPONSE_LEN = 4 + 1 + 1 + 32

DEFAULT_PORT = 8484
HANDSHAKE_TIMEOUT = 5.0
SEND_TIMEOUT = 5.0


class HandshakeError(Exception):
    pass


def _recv_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise HandshakeError("connection closed during handshake")
        data += chunk
    return data


def handshake(sock, key, compress=True):
    """Authenticate a freshly accepted bridge. Returns the negotiated flags."""
    sock.settimeout(HANDSHAKE_TIMEOUT)
    nonce = os.urandom(NONCE_LEN)
    try:
        sock.sendall(MAGIC + bytes([VERSION]) + nonce)
        reply = _recv_exact(sock, RESPONSE_LEN)
    except OSError as exc:
        raise HandshakeError("no answer to challenge (%s)" % exc)
    if reply[:4] != MAGIC or reply[4] != VERSION:
        raise HandshakeError("not a compatible bridge")
    expected = hmac.new(key.encode(), nonce, hashlib.sha256).digest()
    if not hmac.compare_digest(reply[6:], expected):
        raise HandshakeError("wrong key")
    flags = reply[5] & FLAG_DEFLATE if compress else 0
    sock.sendall(bytes([flags]))
    return flags


class NetPort:
    """Gives a connected bridge the same read/write interface as a serial port."""

    def __init__(self, sock, flags=0, read_timeout=0.2):
        self._sock = sock
        self._read_timeout = read_timeout
        self._deflater = zlib.compressobj(6, zlib.DEFLATED, -15) if flags & FLAG_DEFLATE else None
        sock.settimeout(SEND_TIMEOUT)
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.wire_bytes = 0

    def read(self, count):
        ready, _, _ = select.select([self._sock], [], [], self._read_timeout)
        if not ready:
            return b""
        data = self._sock.recv(count)
        if not data:
            raise ConnectionError("bridge closed the connection")
        return data

    def write(self, data):
        if self._deflater:
            data = self._deflater.compress(data) + self._deflater.flush(zlib.Z_SYNC_FLUSH)
        self._sock.sendall(data)
        self.wire_bytes += len(data)

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


def parse_listen(value):
    """'', '8484', ':8484' or '192.168.1.5:8484' -> (address, port)."""
    address, _, port = value.rpartition(":")
    return address, int(port) if port else DEFAULT_PORT


class Listener:
    """Waits for bridges to dial in."""

    def __init__(self, address, port, key, compress=True, log=print):
        self.key, self.compress, self.log = key, compress, log
        self._server = socket.create_server((address, port))
        self._server.settimeout(1.0)
        self.port = self._server.getsockname()[1]

    def accept(self):
        """Returns (NetPort, description) for an authenticated bridge, or None after ~1 s."""
        try:
            sock, peer = self._server.accept()
        except socket.timeout:
            return None
        try:
            flags = handshake(sock, self.key, self.compress)
        except (HandshakeError, OSError) as exc:
            self.log("Rejected connection from %s: %s" % (peer[0], exc))
            sock.close()
            return None
        label = "%s%s" % (peer[0], " (compressed)" if flags & FLAG_DEFLATE else "")
        return NetPort(sock, flags), "Wi-Fi bridge at " + label

    def close(self):
        self._server.close()
