/*
 * Native test harness for calc/src/decode.c.
 * stdin: a sequence of host->calculator messages. stdout: the final framebuffer.
 * argv[1]: refill chunk size, to exercise ops that straddle USB reads.
 */
#include <stdio.h>
#include <stdlib.h>

#include "decode.h"
#include "proto.h"

#define FB_SIZE (320 * 240)

static uint8_t fb[FB_SIZE];
static uint8_t chunk[4096];
static size_t chunk_size;

static bool more(rd_stream_t *s) {
    size_t n = fread(chunk, 1, chunk_size, stdin);
    if (!n) return false;
    s->p = chunk;
    s->end = chunk + n;
    return true;
}

int main(int argc, char **argv) {
    rd_stream_t s = {chunk, chunk, more};
    uint8_t type, palette[512];

    chunk_size = argc > 1 ? (size_t)atoi(argv[1]) : sizeof chunk;
    if (chunk_size < 1 || chunk_size > sizeof chunk) return 2;

    while (rd_read(&s, &type, 1)) {
        if (type == MSG_PALETTE) {
            if (!rd_read(&s, palette, sizeof palette)) return 3;
        } else if (type == MSG_FRAME) {
            if (!rd_decode_frame(&s, fb, FB_SIZE)) return 4;
        } else {
            return 5;
        }
    }
    fwrite(fb, 1, FB_SIZE, stdout);
    return 0;
}
