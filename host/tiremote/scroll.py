"""Scroll detection: find a block of the old screen that reappears shifted
vertically in the new one, so it can be moved on the calculator instead of
being sent again."""

import numpy as np

MIN_ROWS = 8  # smaller moves are not worth the bookkeeping
MAX_SHIFT = 127  # dy travels as a signed byte

_WEIGHTS = np.random.default_rng(84).integers(1, 2**62, 320, dtype=np.uint64) | np.uint64(1)


def _longest_run(mask):
    """(start, length) of the longest run of True in a 1-D bool array."""
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, ends = edges[0::2], edges[1::2]
    if not starts.size:
        return 0, 0
    best = int((ends - starts).argmax())
    return int(starts[best]), int(ends[best] - starts[best])


def find_scroll(new, old):
    """`new`, `old`: (rows, stride) uint8 framebuffers.

    Returns (xb, y, wb, h, dy) meaning "rows [y, y+h) of byte columns
    [xb, xb+wb) of `old` appear dy rows lower in `new`", or None.
    """
    diff = new != old
    rows = np.flatnonzero(diff.any(axis=1))
    if rows.size < MIN_ROWS:
        return None
    cols = np.flatnonzero(diff.any(axis=0))
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    if y1 - y0 < 2 * MIN_ROWS:
        return None

    a = old[y0:y1, x0:x1]
    b = new[y0:y1, x0:x1]
    weights = _WEIGHTS[:x1 - x0]
    hash_a = (a.astype(np.uint64) * weights).sum(axis=1, dtype=np.uint64)
    hash_b = (b.astype(np.uint64) * weights).sum(axis=1, dtype=np.uint64)

    # Vote on the shift using rows whose content is unique in the old picture
    # (blank lines match everywhere and say nothing).
    order = np.argsort(hash_a, kind="stable")
    sorted_a = hash_a[order]
    left = np.searchsorted(sorted_a, hash_b, "left")
    right = np.searchsorted(sorted_a, hash_b, "right")
    unique = (right - left) == 1
    if unique.sum() < MIN_ROWS:
        return None
    shifts = np.flatnonzero(unique) - order[left[unique]]
    shifts = shifts[(shifts != 0) & (np.abs(shifts) <= MAX_SHIFT)]
    if shifts.size < MIN_ROWS:
        return None
    values, counts = np.unique(shifts, return_counts=True)
    dy = int(values[counts.argmax()])
    if counts.max() < MIN_ROWS:
        return None

    # Confirm with a real comparison and take the longest block that moved intact.
    height = y1 - y0
    lo, hi = max(0, dy), height + min(0, dy)  # destination rows that have a source
    if hi - lo < MIN_ROWS:
        return None
    same = (b[lo:hi] == a[lo - dy:hi - dy]).all(axis=1)
    start, length = _longest_run(same)
    if length < MIN_ROWS:
        return None
    return x0, y0 + lo + start - dy, x1 - x0, length, dy
