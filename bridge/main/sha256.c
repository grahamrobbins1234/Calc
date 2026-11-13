/*
 * Small self-contained SHA-256 / HMAC-SHA-256 (FIPS 180-4, RFC 2104), so the
 * bridge does not depend on which crypto API a given ESP-IDF release ships.
 */
#include "sha256.h"

#include <string.h>

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void compress(sha256_t *s, const uint8_t *p) {
    uint32_t w[64], v[8];
    int i;

    for (i = 0; i < 16; i++, p += 4)
        w[i] = (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
    for (; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    memcpy(v, s->state, sizeof v);
    for (i = 0; i < 64; i++) {
        uint32_t s1 = ROR(v[4], 6) ^ ROR(v[4], 11) ^ ROR(v[4], 25);
        uint32_t ch = (v[4] & v[5]) ^ (~v[4] & v[6]);
        uint32_t t1 = v[7] + s1 + ch + K[i] + w[i];
        uint32_t s0 = ROR(v[0], 2) ^ ROR(v[0], 13) ^ ROR(v[0], 22);
        uint32_t maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
        uint32_t t2 = s0 + maj;
        v[7] = v[6]; v[6] = v[5]; v[5] = v[4]; v[4] = v[3] + t1;
        v[3] = v[2]; v[2] = v[1]; v[1] = v[0]; v[0] = t1 + t2;
    }
    for (i = 0; i < 8; i++) s->state[i] += v[i];
}

void tird_sha256_init(sha256_t *s) {
    static const uint32_t init[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(s->state, init, sizeof init);
    s->length = 0;
    s->fill = 0;
}

void tird_sha256_update(sha256_t *s, const uint8_t *data, size_t len) {
    s->length += len;
    while (len) {
        size_t n = 64 - s->fill;
        if (n > len) n = len;
        memcpy(s->block + s->fill, data, n);
        s->fill += n;
        data += n;
        len -= n;
        if (s->fill == 64) {
            compress(s, s->block);
            s->fill = 0;
        }
    }
}

void tird_sha256_final(sha256_t *s, uint8_t out[SHA256_LEN]) {
    uint64_t bits = s->length * 8;
    uint8_t pad[72] = {0x80};
    uint8_t len_be[8];
    size_t pad_len = (s->fill < 56 ? 56 : 120) - s->fill;
    int i;

    for (i = 0; i < 8; i++) len_be[i] = (uint8_t)(bits >> (56 - 8 * i));
    tird_sha256_update(s, pad, pad_len);
    tird_sha256_update(s, len_be, 8);
    for (i = 0; i < 8; i++) {
        out[4 * i] = (uint8_t)(s->state[i] >> 24);
        out[4 * i + 1] = (uint8_t)(s->state[i] >> 16);
        out[4 * i + 2] = (uint8_t)(s->state[i] >> 8);
        out[4 * i + 3] = (uint8_t)s->state[i];
    }
}

void tird_hmac_sha256(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t msg_len,
                 uint8_t out[SHA256_LEN]) {
    sha256_t s;
    uint8_t block[64] = {0}, pad[64], inner[SHA256_LEN];
    int i;

    if (key_len > 64) {
        tird_sha256_init(&s);
        tird_sha256_update(&s, key, key_len);
        tird_sha256_final(&s, block);
    } else {
        memcpy(block, key, key_len);
    }

    for (i = 0; i < 64; i++) pad[i] = block[i] ^ 0x36;
    tird_sha256_init(&s);
    tird_sha256_update(&s, pad, 64);
    tird_sha256_update(&s, msg, msg_len);
    tird_sha256_final(&s, inner);

    for (i = 0; i < 64; i++) pad[i] = block[i] ^ 0x5c;
    tird_sha256_init(&s);
    tird_sha256_update(&s, pad, 64);
    tird_sha256_update(&s, inner, SHA256_LEN);
    tird_sha256_final(&s, out);
}
