#include "link.h"

#include <string.h>

#include "sha256.h"

bool link_make_response(const uint8_t challenge[LINK_CHALLENGE_LEN], const char *key,
                        uint8_t flags, uint8_t response[LINK_RESPONSE_LEN]) {
    if (memcmp(challenge, LINK_MAGIC, 4) != 0 || challenge[4] != LINK_VERSION) return false;

    memcpy(response, LINK_MAGIC, 4);
    response[4] = LINK_VERSION;
    response[5] = flags;
    tird_hmac_sha256((const uint8_t *)key, strlen(key), challenge + 5, LINK_NONCE_LEN, response + 6);
    return true;
}

void link_rx_init(link_rx_t *rx, bool deflate) {
    rx->deflate = deflate;
    rx->pos = 0;
    tinfl_init(&rx->inflator);
}

bool link_rx_feed(link_rx_t *rx, const uint8_t *data, size_t len, link_sink_t sink, void *ctx) {
    if (!rx->deflate) return len == 0 || sink(data, len, ctx);

    while (len) {
        size_t in_len = len;
        size_t out_len = TINFL_LZ_DICT_SIZE - rx->pos;
        tinfl_status status = tinfl_decompress(&rx->inflator, data, &in_len, rx->window,
                                               rx->window + rx->pos, &out_len,
                                               TINFL_FLAG_HAS_MORE_INPUT);
        data += in_len;
        len -= in_len;

        if (out_len && !sink(rx->window + rx->pos, out_len, ctx)) return false;
        rx->pos = (rx->pos + out_len) & (TINFL_LZ_DICT_SIZE - 1);

        /* The host never ends the stream, so DONE is as wrong as FAILED. */
        if (status <= TINFL_STATUS_DONE) return false;
        if (status == TINFL_STATUS_NEEDS_MORE_INPUT && in_len == 0 && out_len == 0) break;
    }
    return true;
}
