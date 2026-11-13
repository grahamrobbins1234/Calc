/*
 * Frame decoder, packet reader, LZ4 block decoder and pointer drawing.
 * Plain C with no calculator dependencies: see decode.h.
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

/* ---- frames ----------------------------------------------------------- */

#define NEXT_BYTE(var)                                   \
    do {                                                 \
        if (s->p == s->end && !s->more(s)) return false; \
        (var) = *s->p++;                                 \
    } while (0)

/* Scroll a block of rows. All fields are validated against the framebuffer. */
static bool move_rows(uint8_t *fb, unsigned int fb_size, unsigned int stride, const uint8_t *arg) {
    unsigned int xb = arg[0] | ((unsigned int)arg[1] << 8);
    unsigned int y = arg[2];
    unsigned int wb = arg[3] | ((unsigned int)arg[4] << 8);
    unsigned int h = arg[5];
    int dy = (int8_t)arg[6];
    unsigned int rows = fb_size / stride;
    unsigned int i;

    if (!wb || !h || !dy || xb + wb > stride || y + h > rows) return false;
    if (dy < 0 ? (unsigned int)-dy > y : y + h + (unsigned int)dy > rows) return false;

    /* Copy in the order that never overwrites a row before it has been read. */
    for (i = 0; i < h; i++) {
        unsigned int row = dy < 0 ? y + i : y + h - 1 - i;
        uint8_t *src = fb + row * stride + xb;
        memcpy(src + dy * (int)stride, src, wb);
    }
    return true;
}

bool rd_decode_frame(rd_stream_t *s, uint8_t *fb, unsigned int fb_size, unsigned int stride,
                     void (*touch)(void)) {
    uint8_t *dst = fb;
    unsigned int left = fb_size;

    for (;;) {
        uint8_t header, op;
        unsigned int count;

        NEXT_BYTE(header);
        if (header == OP_END) return true;
        if (header == OP_MOVE) {
            uint8_t arg[7];
            if (!rd_read(s, arg, sizeof arg)) return false;
            if (touch) {
                touch();
                touch = NULL;
            }
            if (!move_rows(fb, fb_size, stride, arg)) return false;
            continue;
        }

        op = header & 0xC0;
        if (op == 0xC0) return false; /* unknown extended op */
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
            dst += count;
            continue;
        }
        if (touch) {
            touch();
            touch = NULL;
        }
        if (op == OP_RUN) {
            uint8_t value;
            NEXT_BYTE(value);
            memset(dst, value, count);
        } else { /* OP_LIT */
            if (!rd_read(s, dst, count)) return false;
        }
        dst += count;
    }
}

/* ---- LZ4 -------------------------------------------------------------- */

bool lz4_decode(const uint8_t *src, unsigned int src_len, uint8_t *dst, unsigned int dst_len) {
    const uint8_t *ip = src, *iend = src + src_len;
    uint8_t *op = dst, *oend = dst + dst_len;

    for (;;) {
        unsigned int token, len, offset;
        uint8_t b;

        if (ip == iend) return false;
        token = *ip++;

        len = token >> 4;
        if (len == 15) {
            do {
                if (ip == iend) return false;
                b = *ip++;
                len += b;
            } while (b == 255);
        }
        if (len > (unsigned int)(iend - ip) || len > (unsigned int)(oend - op)) return false;
        memcpy(op, ip, len);
        ip += len;
        op += len;
        if (ip == iend) return op == oend; /* the last sequence is literals only */

        if (iend - ip < 2) return false;
        offset = ip[0] | ((unsigned int)ip[1] << 8);
        ip += 2;
        if (!offset || offset > (unsigned int)(op - dst)) return false;

        len = token & 15;
        if (len == 15) {
            do {
                if (ip == iend) return false;
                b = *ip++;
                len += b;
            } while (b == 255);
        }
        len += 4;
        if (len > (unsigned int)(oend - op)) return false;

        if (offset >= len) {
            memcpy(op, op - offset, len);
            op += len;
        } else if (offset == 1) {
            memset(op, op[-1], len);
            op += len;
        } else {
            const uint8_t *match = op - offset;
            while (len--) *op++ = *match++;
        }
    }
}

/* ---- packets ---------------------------------------------------------- */

static pk_read_fn pk_read;
static uint8_t *pk_wire, *pk_plain;

static bool pk_more(rd_stream_t *s) {
    uint8_t head[2];
    unsigned int n, plain_len;

    if (!pk_read(head, 2)) return false;
    n = head[0] | ((unsigned int)head[1] << 8);

    if (n & PKT_LZ4) {
        n &= ~(unsigned int)PKT_LZ4;
        if (!pk_read(head, 2)) return false;
        plain_len = head[0] | ((unsigned int)head[1] << 8);
        if (!n || n > PKT_MAX || !plain_len || plain_len > PKT_MAX) return false;
        if (!pk_read(pk_wire, n)) return false;
        if (!lz4_decode(pk_wire, n, pk_plain, plain_len)) return false;
    } else {
        if (!n || n > PKT_MAX) return false;
        if (!pk_read(pk_plain, n)) return false;
        plain_len = n;
    }
    s->p = pk_plain;
    s->end = pk_plain + plain_len;
    return true;
}

void pk_init(rd_stream_t *s, pk_read_fn read, uint8_t *wire, uint8_t *plain) {
    pk_read = read;
    pk_wire = wire;
    pk_plain = plain;
    s->p = s->end = plain;
    s->more = pk_more;
}

/* ---- pointer ---------------------------------------------------------- */

/* 0 = transparent, 1 = outline (darkest colour), 2 = fill (brightest colour). */
static const uint8_t cursor_art[CURSOR_H][CURSOR_W] = {
    {1, 0, 0, 0, 0, 0, 0, 0},
    {1, 1, 0, 0, 0, 0, 0, 0},
    {1, 2, 1, 0, 0, 0, 0, 0},
    {1, 2, 2, 1, 0, 0, 0, 0},
    {1, 2, 2, 2, 1, 0, 0, 0},
    {1, 2, 2, 2, 2, 1, 0, 0},
    {1, 2, 2, 2, 2, 2, 1, 0},
    {1, 2, 2, 2, 2, 2, 2, 1},
    {1, 2, 2, 2, 1, 1, 1, 1},
    {1, 2, 1, 2, 1, 0, 0, 0},
    {1, 1, 0, 1, 2, 1, 0, 0},
    {0, 0, 0, 0, 1, 1, 0, 0},
};

/* Which framebuffer bytes of one row the pointer covers. */
static unsigned int cursor_span(const cursor_t *c, unsigned int *first) {
    unsigned int stride = SCREEN_W * c->bpp / 8;
    unsigned int start = c->x * c->bpp / 8;
    unsigned int end = ((c->x + CURSOR_W) * c->bpp + 7) / 8;
    if (end > stride) end = stride;
    *first = start;
    return end - start;
}

void cursor_show(cursor_t *c, uint8_t *fb, uint8_t bpp, unsigned int x, unsigned int y) {
    unsigned int stride = SCREEN_W * bpp / 8;
    unsigned int first, span, row, col;
    uint8_t mask = (uint8_t)((1u << bpp) - 1); /* also the brightest colour index */

    if (c->shown) cursor_hide(c, fb);
    if (x >= SCREEN_W || y >= SCREEN_H) return;
    c->x = x;
    c->y = y;
    c->bpp = bpp;
    c->shown = true;
    span = cursor_span(c, &first);

    for (row = 0; row < CURSOR_H && y + row < SCREEN_H; row++) {
        uint8_t *line = fb + (y + row) * stride;
        memcpy(c->saved[row], line + first, span);
        for (col = 0; col < CURSOR_W && x + col < SCREEN_W; col++) {
            uint8_t art = cursor_art[row][col];
            unsigned int bit = (x + col) * bpp; /* leftmost pixel in the lowest bits */
            uint8_t shift = bit & 7;
            uint8_t *p = line + bit / 8;
            if (!art) continue;
            *p = (uint8_t)((*p & ~(mask << shift)) | ((art == 2 ? mask : 0) << shift));
        }
    }
}

void cursor_hide(cursor_t *c, uint8_t *fb) {
    unsigned int stride = SCREEN_W * c->bpp / 8;
    unsigned int first, span, row;

    if (!c->shown) return;
    c->shown = false;
    span = cursor_span(c, &first);
    for (row = 0; row < CURSOR_H && c->y + row < SCREEN_H; row++)
        memcpy(fb + (c->y + row) * stride + first, c->saved[row], span);
}
