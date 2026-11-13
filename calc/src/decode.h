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
 * Returns false if the stream ran dry or the data would overrun `fb`.
 */
bool rd_decode_frame(rd_stream_t *s, uint8_t *fb, unsigned int fb_size);

#endif
