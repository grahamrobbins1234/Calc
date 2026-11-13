"""Delta + run-length frame encoder (and a reference decoder for tests).

The calculator's CPU is the bottleneck, so the format is built for a decoder
that is nothing but skip / memset / memcpy. See calc/src/proto.h.
"""

import numpy as np

from .protocol import FB_SIZE, MSG_FRAME, OP_END, OP_LIT, OP_RUN, OP_SKIP

MIN_SKIP = 4  # unchanged gaps shorter than this are cheaper to resend
MIN_RUN = 4  # equal-colour runs shorter than this are cheaper as literals

EMPTY_FRAME = bytes([MSG_FRAME, OP_END])


def _run_starts(boundary):
    """Indices where a new run starts, given a per-pixel 'differs from previous' mask."""
    return np.concatenate([[0], np.flatnonzero(boundary) + 1])


def encode_frame(new, old=None):
    """Encode `new` (flat uint8 framebuffer) as a frame message.

    `old` is what the calculator currently shows, or None to send everything.
    """
    new = np.ascontiguousarray(new, dtype=np.uint8).ravel()
    n = new.size
    if old is None:
        changed = np.ones(n, dtype=bool)
    else:
        changed = new != np.asarray(old).ravel()
        if not changed.any():
            return EMPTY_FRAME
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

    return out.tobytes()


def decode_frame(data, fb):
    """Reference decoder: apply a frame message to `fb` (flat uint8 array) in place."""
    if data[0] != MSG_FRAME:
        raise ValueError("not a frame message")
    pos, i = 0, 1
    while True:
        header = data[i]
        i += 1
        op = header & 0xC0
        if op == OP_END:
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


assert FB_SIZE // 2 <= 0xFFFF
