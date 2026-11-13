/*
 * The network leg of the remote desktop link (bridge <-> PC host).
 * Mirrored by host/tiremote/net.py. No ESP-IDF dependencies, so the host's
 * test-suite compiles this file natively and talks to it.
 *
 * The bridge dials the host over TCP, then:
 *
 *   host   -> bridge  "TIRD" version nonce[16]                 (challenge)
 *   bridge -> host    "TIRD" version flags hmac[32]            (response)
 *   host   -> bridge  flags                                    (accepted features)
 *
 * hmac = HMAC-SHA256(shared key, nonce). After that the connection is a
 * plain pipe for the calculator's serial protocol. If LINK_FLAG_DEFLATE was
 * accepted, the host -> bridge direction is one continuous raw deflate
 * stream, sync-flushed after every message.
 */
#ifndef LINK_H
#define LINK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "miniz.h"

#define LINK_MAGIC "TIRD"
#define LINK_VERSION 1
#define LINK_FLAG_DEFLATE 0x01

#define LINK_NONCE_LEN 16
#define LINK_CHALLENGE_LEN (4 + 1 + LINK_NONCE_LEN)
#define LINK_RESPONSE_LEN (4 + 1 + 1 + 32)

/* Answer the host's challenge. Returns false if the challenge is not one we understand. */
bool link_make_response(const uint8_t challenge[LINK_CHALLENGE_LEN], const char *key,
                        uint8_t flags, uint8_t response[LINK_RESPONSE_LEN]);

/* Receives decoded bytes for the calculator. Return false to abort. */
typedef bool (*link_sink_t)(const uint8_t *data, size_t len, void *ctx);

typedef struct {
    bool deflate;
    size_t pos;
    tinfl_decompressor inflator;
    uint8_t window[TINFL_LZ_DICT_SIZE];
} link_rx_t;

void link_rx_init(link_rx_t *rx, bool deflate);

/* Feed bytes received from the host. Returns false on a corrupt stream or if the sink aborted. */
bool link_rx_feed(link_rx_t *rx, const uint8_t *data, size_t len, link_sink_t sink, void *ctx);

#endif
