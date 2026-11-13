"""Delta + run-length frame encoder (and a reference decoder for tests).

The calculator's CPU is the bottleneck, so the format is built for a decoder
that is nothing but skip / memset / memcpy. See calc/src/proto.h.
"""

import numpy as np

import struct

import lz4.block as lz4_block

from .protocol import (MSG_FRAME, OP_END, OP_LIT, OP_MOVE, OP_RUN, OP_SKIP, PKT_LZ4, PKT_MAX)

MIN_SKIP = 4  # unchanged gaps shorter than this are cheaper to resend
MIN_RUN = 4  # equal-colour runs shorter than this are cheaper as literals

EMPTY_FRAME = bytes([MSG_FRAME, OP_END])


def _run_starts(boundary):
    """Indices where a new run starts, given a per-pixel 'differs from previous' mask."""
    return np.concatenate([[0], np.flatnonzero(boundary) + 1])


def move_op(xb, y, wb, h, dx, dy):
    """Frame op: copy rows [y, y+h) x byte columns [xb, xb+wb) to a place
    dx bytes right and dy rows down."""
    return struct.pack("<BHBHBbb", OP_MOVE, xb, y, wb, h, dx, dy)


def move_is_valid(rows, stride, xb, y, wb, h, dx, dy):
    return bool(wb > 0 and h > 0 and (dx or dy)
                and -128 <= dx <= 127 and -128 <= dy <= 127
                and xb >= 0 and y >= 0 and xb + wb <= stride and y + h <= rows
                and 0 <= xb + dx and xb + wb + dx <= stride
                and 0 <= y + dy and y + h + dy <= rows)


def apply_move(fb2d, xb, y, wb, h, dx, dy):
    """What move_op does, applied to a (rows, stride) array in place."""
    fb2d[y + dy:y + dy + h, xb + dx:xb + dx + wb] = fb2d[y:y + h, xb:xb + wb].copy()


def encode_frame(new, old=None, prefix=b""):
    """Encode `new` (flat uint8 framebuffer) as a frame message.

    `old` is what the calculator currently shows, or None to send everything.
    `prefix` is ops (move_op) to run first; `old` must already reflect them.
    """
    new = np.ascontiguousarray(new, dtype=np.uint8).ravel()
    n = new.size
    if old is None:
        changed = np.ones(n, dtype=bool)
    else:
        changed = new != np.asarray(old).ravel()
        if not changed.any():
            return bytes([MSG_FRAME]) + prefix + bytes([OP_END])
        # Absorb short unchanged gaps between changed pixels.
        starts = _run_starts(changed[1:] != changed[:-1])
        lens = np.diff(np.concatenate([starts, [n]]))
        vals = changed[starts]
        short = ~vals & (lens < MIN_SKIP)
        short[0] = short[-1] = False
        if short.any():
            changed = np.repeat(vals | short, lens)

    # Split into runs: unchanged stretches, and equal-colour stretches of changed pixels.
    boundary = changed[1:] != changed[:-1]
    boundary |= changed[1:] & (new[1:] != new[:-1])
    boundary[n // 2 - 1] = True  # keeps every count within 16 bits
    starts = _run_starts(boundary)
    lens = np.diff(np.concatenate([starts, [n]]))
    is_changed = changed[starts]

    ops = np.where(is_changed, np.where(lens >= MIN_RUN, OP_RUN, OP_LIT), OP_SKIP)

    # Merge neighbouring literal runs into one op (but not across the midpoint).
    keep = np.ones(starts.size, dtype=bool)
    keep[1:] = ~((ops[1:] == OP_LIT) & (ops[:-1] == OP_LIT))
    keep[np.searchsorted(starts, n // 2)] = True
    starts, ops = starts[keep], ops[keep]
    lens = np.diff(np.concatenate([starts, [n]]))

    if ops[-1] == OP_SKIP:  # a trailing skip is implied by OP_END
        starts, ops, lens = starts[:-1], ops[:-1], lens[:-1]

    is_long = lens > 63
    header_size = np.where(is_long, 3, 1)
    payload_size = np.where(ops == OP_LIT, lens, np.where(ops == OP_RUN, 1, 0))
    sizes = header_size + payload_size
    offsets = 1 + np.concatenate([[0], np.cumsum(sizes)[:-1]])
    total = 1 + int(sizes.sum()) + 1

    out = np.zeros(total, dtype=np.uint8)
    out[0] = MSG_FRAME
    out[-1] = OP_END
    out[offsets] = ops | np.where(is_long, 0, lens)
    long_offsets, long_lens = offsets[is_long], lens[is_long]
    out[long_offsets + 1] = long_lens & 0xFF
    out[long_offsets + 2] = long_lens >> 8

    payload = offsets + header_size
    runs = ops == OP_RUN
    out[payload[runs]] = new[starts[runs]]

    lits = ops == OP_LIT
    if lits.any():
        src = np.flatnonzero(np.repeat(lits, lens))
        dst = np.repeat(payload[lits] - starts[lits], lens[lits]) + src
        out[dst] = new[src]

    body = out.tobytes()
    return body[:1] + prefix + body[1:]


def packetize(data, lz4=True):
    """Wrap host -> calculator bytes in packets, LZ4-compressing where it pays."""
    out = []
    for start in range(0, len(data), PKT_MAX):
        chunk = data[start:start + PKT_MAX]
        if lz4 and len(chunk) > 32:
            packed = lz4_block.compress(chunk, mode="high_compression", store_size=False)
            if len(packed) + 2 < len(chunk):
                out.append(struct.pack("<HH", len(packed) | PKT_LZ4, len(chunk)) + packed)
                continue
        out.append(struct.pack("<H", len(chunk)) + chunk)
    return b"".join(out)


def unpacketize(read):
    """Reference packet reader. `read(n)` returns exactly n bytes. Returns one payload."""
    (n,) = struct.unpack("<H", read(2))
    if n & PKT_LZ4:
        (size,) = struct.unpack("<H", read(2))
        n &= ~PKT_LZ4
        if not 0 < n <= PKT_MAX or not 0 < size <= PKT_MAX:
            raise ValueError("bad packet header")
        return lz4_block.decompress(read(n), uncompressed_size=size)
    if not 0 < n <= PKT_MAX:
        raise ValueError("bad packet header")
    return read(n)


def decode_frame(data, fb, stride=320):
    """Reference decoder: apply a frame message to `fb` (flat uint8 array) in place."""
    if data[0] != MSG_FRAME:
        raise ValueError("not a frame message")
    pos, i = 0, 1
    while True:
        header = data[i]
        i += 1
        if header == OP_MOVE:
            move = struct.unpack_from("<HBHBbb", data, i)
            i += 8
            rows = fb.size // stride
            if not move_is_valid(rows, stride, *move):
                raise ValueError("bad move")
            apply_move(fb.reshape(rows, stride), *move)
            continue
        op = header & 0xC0
        if op == OP_END:
            if header != OP_END:
                raise ValueError("unknown op")
            if i != len(data):
                raise ValueError("trailing data after OP_END")
            return fb
        count = header & 0x3F
        if count == 0:
            count = data[i] | (data[i + 1] << 8)
            i += 2
        if pos + count > fb.size:
            raise ValueError("frame overruns framebuffer")
        if op == OP_RUN:
            fb[pos:pos + count] = data[i]
            i += 1
        elif op == OP_LIT:
            fb[pos:pos + count] = np.frombuffer(data, dtype=np.uint8, count=count, offset=i)
            i += count
        pos += count

