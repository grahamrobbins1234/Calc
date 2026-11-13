/*
 * Native test harness for calc/src/decode.c: behaves like the calculator's
 * serve() loop on a host -> calculator byte stream read from stdin.
 *
 * stdout: bpp, cursor x (2 bytes), cursor y, then the framebuffer with the
 * pointer lifted, then the framebuffer with the pointer drawn.
 * Exit status 0 on clean end of input, 4 if the stream was rejected
 * (the reason, rd_error, goes to stderr).
 */
#include <stdio.h>
#include <string.h>

#include "decode.h"
#include "proto.h"

static uint8_t fb[SCREEN_W * SCREEN_H];
static uint8_t wire[PKT_MAX], plain[PKT_MAX];
static cursor_t cursor;
static uint8_t bpp = 8;
static unsigned int cursor_x, cursor_y = CURSOR_HIDDEN;
static int at_eof;

static bool read_exact(uint8_t *dst, unsigned int len) {
    size_t n = fread(dst, 1, len, stdin);
    if (n == 0 && len) at_eof = 1;
    return n == len;
}

static void pointer_hide(void) {
    cursor_hide(&cursor, fb);
}

static void pointer_show(void) {
    if (cursor_y != CURSOR_HIDDEN) cursor_show(&cursor, fb, bpp, cursor_x, cursor_y);
}

static void pointer_moved(const uint8_t *note) {
    cursor_x = note[0] | ((unsigned int)note[1] << 8);
    cursor_y = note[2];
    pointer_hide();
    pointer_show();
    rd_retouch = true;
}

static bool serve(rd_stream_t *s) {
    for (;;) {
        uint8_t type, arg[1], palette[512];
        unsigned int count;

        at_eof = 0;
        if (!rd_read(s, &type, 1)) return at_eof; /* running out between messages is fine */

        switch (type) {
        case MSG_MODE:
            if (!rd_read(s, arg, 1)) return false;
            if (arg[0] != 8 && arg[0] != 4 && arg[0] != 1) return false;
            cursor.shown = false;
            bpp = arg[0];
            break;
        case MSG_PALETTE:
            if (!rd_read(s, arg, 1)) return false;
            count = arg[0] ? arg[0] : 256;
            if (!rd_read(s, palette, count * 2)) return false;
            break;
        case MSG_FRAME:
            if (!rd_decode_frame(s, fb, SCREEN_W * SCREEN_H * bpp / 8, SCREEN_W * bpp / 8,
                                 pointer_hide))
                return false;
            pointer_show();
            break;
        default:
            return false;
        }
    }
}

int main(void) {
    rd_stream_t s;
    unsigned int size;
    uint8_t head[4];

    memset(fb, 0xA5, sizeof fb); /* video memory starts with leftovers */
    pk_init(&s, read_exact, pointer_moved, wire, plain);
    if (!serve(&s)) {
        fprintf(stderr, "%d", rd_error);
        return 4;
    }

    size = SCREEN_W * SCREEN_H * bpp / 8;
    head[0] = bpp;
    head[1] = (uint8_t)cursor_x;
    head[2] = (uint8_t)(cursor_x >> 8);
    head[3] = (uint8_t)cursor_y;
    fwrite(head, 1, sizeof head, stdout);

    pointer_hide();
    fwrite(fb, 1, size, stdout);
    pointer_show();
    fwrite(fb, 1, size, stdout);
    return 0;
}
