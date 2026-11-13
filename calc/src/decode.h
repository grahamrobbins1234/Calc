/*
 * Portable (no calculator headers) decoding code, so the host's test-suite
 * can compile these files natively and check them against the Python side.
 */
#ifndef DECODE_H
#define DECODE_H

#include <stdbool.h>
#include <stdint.h>

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
 * `touch`, if not NULL, is called once before the first change to `fb`
 * (and not at all for an empty frame).
 * Returns false if the stream ran dry or the data is invalid.
 */
bool rd_decode_frame(rd_stream_t *s, uint8_t *fb, unsigned int fb_size, unsigned int stride,
                     void (*touch)(void));

/* ---- packets ---------------------------------------------------------- */

/* Reads exactly `len` raw bytes from the link, or returns false. */
typedef bool (*pk_read_fn)(uint8_t *dst, unsigned int len);

/*
 * Make `s` a stream of packet payloads. `wire` and `plain` are scratch
 * buffers of PKT_MAX bytes each. One packet stream at a time.
 */
void pk_init(rd_stream_t *s, pk_read_fn read, uint8_t *wire, uint8_t *plain);

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
