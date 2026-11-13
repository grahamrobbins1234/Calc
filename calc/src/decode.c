/*
 * Frame decoder. Plain C with no calculator dependencies so the exact same
 * file is also compiled natively and tested against the Python encoder.
 */
#include "decode.h"
#include "proto.h"

#include <string.h>

bool rd_read(rd_stream_t *s, uint8_t *dst, unsigned int len) {
    while (len) {
        unsigned int avail = (unsigned int)(s->end - s->p);
        if (!avail) {
            if (!s->more(s)) return false;
            continue;
        }
        if (avail > len) avail = len;
        memcpy(dst, s->p, avail);
        s->p += avail;
        dst += avail;
        len -= avail;
    }
    return true;
}

#define NEXT_BYTE(var)                                \
    do {                                              \
        if (s->p == s->end && !s->more(s)) return false; \
        (var) = *s->p++;                              \
    } while (0)

bool rd_decode_frame(rd_stream_t *s, uint8_t *fb, unsigned int fb_size) {
    unsigned int left = fb_size;

    for (;;) {
        uint8_t header, op;
        unsigned int count;

        NEXT_BYTE(header);
        op = header & 0xC0;
        if (op == OP_END) return true;

        count = header & 0x3F;
        if (!count) {
            uint8_t lo, hi;
            NEXT_BYTE(lo);
            NEXT_BYTE(hi);
            count = lo | ((unsigned int)hi << 8);
        }
        if (count > left) return false; /* corrupt stream */
        left -= count;

        if (op == OP_SKIP) {
            fb += count;
        } else if (op == OP_RUN) {
            uint8_t colour;
            NEXT_BYTE(colour);
            memset(fb, colour, count);
            fb += count;
        } else { /* OP_LIT */
            if (!rd_read(s, fb, count)) return false;
            fb += count;
        }
    }
}
