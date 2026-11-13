/*
 * Portable (no calculator headers) decoding code, so the host's test-suite
 * can compile these files natively and check them against the Python side.
 */
#ifndef DECODE_H
#define DECODE_H

#include <stdbool.h>
#include <stdint.h>

/*
 * Why the last decoding call failed, for the status screen. Zero means the
 * data simply stopped arriving (timeout, unplugged, or quitting).
 */
extern uint8_t rd_error;
#define RD_ERR_NONE    0
#define RD_ERR_OP      2 /* unknown frame op */
#define RD_ERR_OVERRUN 3 /* frame runs past the end of the screen */
#define RD_ERR_MOVE    4 /* move op outside the screen */
#define RD_ERR_PACKET  5 /* bad packet length */
#define RD_ERR_LZ4     6 /* corrupt compressed packet */
#define RD_ERR_MESSAGE 7 /* unknown message type */
#define RD_ERR_MODE    8 /* unsupported screen mode */

/* A pull-based byte stream. `more` must refill [p, end) or return false. */
typedef struct rd_stream {
    const uint8_t *p;
    const uint8_t *end;
    bool (*more)(struct rd_stream *s);
} rd_stream_t;

/* Read exactly `len` bytes. Returns false if the stream ran dry. */
bool rd_read(rd_stream_t *s, uint8_t *dst, unsigned int len);

/*
 * Decode one frame (everything after the MSG_FRAME byte) into `fb`.
 * `stride` is the number of framebuffer bytes per screen row.
 * `touch`, if not NULL, is called before the first change to `fb` (not at
 * all for an empty frame), and again before the next change whenever
 * something sets rd_retouch in the meantime.
 * Returns false if the stream ran dry or the data is invalid.
 */
bool rd_decode_frame(rd_stream_t *s, uint8_t *fb, unsigned int fb_size, unsigned int stride,
                     void (*touch)(void));

extern bool rd_retouch;

/* The byte range [rd_dirty_lo, rd_dirty_hi) of `fb` the last frame wrote to (empty if lo >= hi). */
extern unsigned int rd_dirty_lo, rd_dirty_hi;

/*
 * Half-resolution mode: `half` holds a 160x120 picture. Draw the rows that
 * cover its bytes [lo, hi) onto the 320x240 `screen`, each pixel doubled
 * in both directions.
 */
void rd_expand(uint8_t *screen, const uint8_t *half, uint8_t bpp, unsigned int lo,
               unsigned int hi);

/* ---- packets ---------------------------------------------------------- */

/* Reads exactly `len` raw bytes from the link, or returns false. */
typedef bool (*pk_read_fn)(uint8_t *dst, unsigned int len);

/* Receives the PKT_OOB_LEN bytes of an out-of-band packet as soon as it arrives. */
typedef void (*pk_oob_fn)(const uint8_t *note);

/*
 * Make `s` a stream of packet payloads. `wire` and `plain` are scratch
 * buffers of PKT_MAX bytes each. One packet stream at a time.
 */
void pk_init(rd_stream_t *s, pk_read_fn read, pk_oob_fn oob, uint8_t *wire, uint8_t *plain);

/* Decompress one LZ4 block that must expand to exactly `dst_len` bytes. */
bool lz4_decode(const uint8_t *src, unsigned int src_len, uint8_t *dst, unsigned int dst_len);

/* ---- pointer ---------------------------------------------------------- */

#define CURSOR_W 8
#define CURSOR_H 12

typedef struct {
    bool shown;
    unsigned int x, y;
    uint8_t bpp;
    uint8_t saved[CURSOR_H][CURSOR_W + 1]; /* framebuffer bytes under the pointer */
} cursor_t;

/* Draw the pointer with its tip at (x, y), remembering what was underneath. */
void cursor_show(cursor_t *c, uint8_t *fb, uint8_t bpp, unsigned int x, unsigned int y);

/* Remove the pointer, restoring what was underneath. */
void cursor_hide(cursor_t *c, uint8_t *fb);

#endif
