/*
 * Native test harness for the bridge's link code (run by host/tests).
 *   link_harness respond KEY   stdin: challenge        stdout: response
 *   link_harness inflate N     stdin: deflate stream   stdout: decoded bytes (fed N at a time)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "link.h"

static link_rx_t rx;

static bool to_stdout(const uint8_t *data, size_t len, void *ctx) {
    (void)ctx;
    return fwrite(data, 1, len, stdout) == len;
}

int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "respond")) {
        uint8_t challenge[LINK_CHALLENGE_LEN], response[LINK_RESPONSE_LEN];
        if (fread(challenge, 1, sizeof challenge, stdin) != sizeof challenge) return 2;
        if (!link_make_response(challenge, argv[2], LINK_FLAG_DEFLATE, response)) return 3;
        fwrite(response, 1, sizeof response, stdout);
        return 0;
    }
    if (argc == 3 && !strcmp(argv[1], "inflate")) {
        static uint8_t buf[4096];
        size_t chunk = (size_t)atoi(argv[2]), n;
        if (chunk < 1 || chunk > sizeof buf) return 2;
        link_rx_init(&rx, true);
        while ((n = fread(buf, 1, chunk, stdin)) > 0)
            if (!link_rx_feed(&rx, buf, n, to_stdout, NULL)) return 4;
        return 0;
    }
    return 1;
}
